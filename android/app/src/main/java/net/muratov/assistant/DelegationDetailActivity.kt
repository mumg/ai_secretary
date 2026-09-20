package net.muratov.assistant

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import net.muratov.assistant.data.remote.DelegationDto
import net.muratov.assistant.ui.*

class DelegationDetailActivity: ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id=intent.getStringExtra("delegation_id") ?: return finish()
        val repository=(application as ImproverApplication).container.repository
        setContent { ImproverTheme {
            var data by remember { mutableStateOf<DelegationDto?>(null) }; var error by remember { mutableStateOf<String?>(null) }; var busy by remember { mutableStateOf(false) }
            val scope=rememberCoroutineScope()
            suspend fun load() { try { data=repository.delegation(id);error=null } catch(e: CancellationException) {throw e} catch(e: Exception) {error=e.message} }
            val revisions by net.muratov.assistant.notifications.RealtimeState.revisions.collectAsState()
            LaunchedEffect(id, revisions["delegations"], revisions["all"]) {load()}
            Surface(Modifier.fillMaxSize()) {
                Column(Modifier.padding(20.dp).verticalScroll(rememberScrollState())) {
                    TextButton(onClick={finish()}) { Text("Назад") }
                    error?.let {Text(it,color=MaterialTheme.colorScheme.error)}
                    TextButton(onClick={scope.launch {load()}},enabled=!busy) {Text("Обновить")}
                    data?.let { d ->
                        Text(d.title,style=MaterialTheme.typography.headlineSmall)
                        Text(d.assigneeName.ifBlank { d.assigneeEmail.ifBlank { "Исполнитель не определён" } })
                        Text("${delegationStatuses[d.status]} · ${d.dueAt ?: "Без срока"}")
                        Text(d.description ?: "")
                        Text("Ожидаемый результат",style=MaterialTheme.typography.titleMedium); Text(d.expectedResult ?: "Не указан")
                        Text("Основание",style=MaterialTheme.typography.titleMedium); Text(d.evidence)
                        d.sourceEventId?.let { event -> TextButton(onClick={startActivity(EventDetailActivity.intent(this@DelegationDetailActivity,event,null))}) {Text("Исходное письмо")} }
                        if(busy) CircularProgressIndicator()
                        else DelegationFilter("Статус",d.status,delegationStatuses.mapValues { (key,value) -> if(key=="COMPLETED") "Принять результат" else value }) { status ->
                            scope.launch {busy=true;try {repository.delegationStatus(id,status);load()} catch(e: CancellationException) {throw e} catch(e: Exception) {error=e.message} finally {busy=false}}
                        }
                        Text("История изменений",style=MaterialTheme.typography.titleMedium)
                        d.history.forEach { h ->
                            Text("${h.oldStatus?.let { (delegationStatuses[it] ?: it) + " → " } ?: ""}${delegationStatuses[h.newStatus]} · ${if(h.actor=="USER") "Пользователь" else "ИИ"} · ${h.createdAt}")
                            Text(h.explanation)
                            h.sourceEventId?.let { event -> TextButton(onClick={startActivity(EventDetailActivity.intent(this@DelegationDetailActivity,event,null))}) {Text("Письмо-основание")} }
                        }
                    }
                }
            }
        } }
    }
    companion object { fun intent(context: Context,id: String)=Intent(context,DelegationDetailActivity::class.java).putExtra("delegation_id",id) }
}
