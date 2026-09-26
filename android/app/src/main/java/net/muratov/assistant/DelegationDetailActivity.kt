package net.muratov.assistant

import net.muratov.assistant.i18n.tr

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import net.muratov.assistant.data.remote.DelegationDto
import net.muratov.assistant.ui.*

class DelegationDetailActivity: net.muratov.assistant.i18n.LocalizedActivity() {
    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id=intent.getStringExtra("delegation_id") ?: return finish()
        val repository=(application as ImproverApplication).container.repository
        setContent { ImproverTheme {
            var data by remember { mutableStateOf<DelegationDto?>(null) }; var error by remember { mutableStateOf<String?>(null) }; var busy by remember { mutableStateOf(false) }
            var refreshing by remember { mutableStateOf(false) }
            val scope=rememberCoroutineScope()
            suspend fun load() { try { data=repository.delegation(id);error=null } catch(e: CancellationException) {throw e} catch(e: Exception) {error=e.message} }
            val revisions by net.muratov.assistant.notifications.RealtimeState.revisions.collectAsState()
            LaunchedEffect(id, revisions["delegations"], revisions["all"]) {load()}
            Scaffold(topBar = {
                TopAppBar(title = { Text(tr("Детали поручения")) }, navigationIcon = {
                    IconButton(onClick = ::finish) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = tr("Назад"))
                    }
                })
            }) { padding ->
                PullToRefreshBox(
                    isRefreshing = refreshing,
                    onRefresh = {
                        if (!refreshing && !busy) scope.launch {
                            refreshing = true
                            try { load() } finally { refreshing = false }
                        }
                    },
                    modifier = Modifier.fillMaxSize().padding(padding),
                ) {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    error?.let {Text(it,color=MaterialTheme.colorScheme.error)}
                    data?.let { d ->
                        Text(d.title,style=MaterialTheme.typography.headlineSmall)
                        if (d.sourceEventId != null) TextButton(onClick = { startActivity(DiagnosticReportActivity.intent(this@DelegationDetailActivity, "delegation", id)) }) { Text(tr("Сообщить об ошибке")) }
                        Text(d.assigneeName.ifBlank { d.assigneeEmail.ifBlank { tr("Исполнитель не определён") } })
                        Text("${delegationStatuses[d.status]} · ${d.dueAt ?: tr("Без срока")}")
                        Text(d.description ?: "")
                        Text(tr("Ожидаемый результат"),style=MaterialTheme.typography.titleMedium); Text(d.expectedResult ?: tr("Не указан"))
                        Text(tr("Основание"),style=MaterialTheme.typography.titleMedium); Text(d.evidence)
                        d.sourceEventId?.let { event -> TextButton(onClick={startActivity(EventDetailActivity.intent(this@DelegationDetailActivity,event,null))}) {Text(tr("Исходное письмо"))} }
                        if(busy) CircularProgressIndicator()
                        else DelegationFilter(tr("Статус"),d.status,delegationStatuses.mapValues { (key,value) -> if(key=="COMPLETED") tr("Принять результат") else value }) { status ->
                            scope.launch {busy=true;try {repository.delegationStatus(id,status);load()} catch(e: CancellationException) {throw e} catch(e: Exception) {error=e.message} finally {busy=false}}
                        }
                        Text(tr("История изменений"),style=MaterialTheme.typography.titleMedium)
                        d.history.forEach { h ->
                            Text("${h.oldStatus?.let { (delegationStatuses[it] ?: it) + " → " } ?: ""}${delegationStatuses[h.newStatus]} · ${if(h.actor=="USER") tr("Пользователь") else tr("ИИ")} · ${h.createdAt}")
                            Text(h.explanation)
                            h.sourceEventId?.let { event -> TextButton(onClick={startActivity(EventDetailActivity.intent(this@DelegationDetailActivity,event,null))}) {Text(tr("Письмо-основание"))} }
                        }
                    }
                }
                }
            }
        } }
    }
    companion object { fun intent(context: Context,id: String)=Intent(context,DelegationDetailActivity::class.java).putExtra("delegation_id",id) }
}
