package net.muratov.assistant.ui

import net.muratov.assistant.i18n.tr

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AssignmentInd
import androidx.compose.material.icons.filled.FilterList
import androidx.compose.ui.Alignment
import androidx.compose.ui.text.style.TextOverflow
import java.time.OffsetDateTime
import java.time.ZoneId
import net.muratov.assistant.i18n.dateTimeFormatter
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import net.muratov.assistant.*
import net.muratov.assistant.data.TaskRepository
import net.muratov.assistant.data.remote.*
import net.muratov.assistant.notifications.observeRealtime

val delegationStatuses get() = linkedMapOf("ASSIGNED" to tr("Назначено"), "IN_PROGRESS" to tr("В работе"), "IN_REVIEW" to tr("На проверке"), "COMPLETED" to tr("Выполнено"), "CANCELLED" to tr("Отменено"))
data class DelegationState(val items: List<DelegationDto> = emptyList(), val recipients: List<DelegationRecipient> = emptyList(), val query: String = "", val assignee: String = "", val status: String = "", val due: String = "", val more: Boolean = false, val loading: Boolean = false, val error: String? = null)
class DelegationsViewModel(private val repository: TaskRepository): ViewModel() {
    private val mutable = MutableStateFlow(DelegationState())
    val state = mutable.asStateFlow()
    private var job: Job? = null
    init { observeRealtime("delegations") { load() }; load() }
    fun filters(query: String = mutable.value.query, assignee: String = mutable.value.assignee, status: String = mutable.value.status, due: String = mutable.value.due) {
        mutable.value = mutable.value.copy(query=query.take(200),assignee=assignee,status=status,due=due,items=emptyList())
        load(debounce=true)
    }
    fun load(more: Boolean = false, debounce: Boolean = false) {
        job?.cancel()
        val before=mutable.value
        job=viewModelScope.launch {
            mutable.value=before.copy(loading=true,error=null)
            try {
                if(debounce) delay(300)
                val page=repository.delegations(before.query,before.assignee,before.status,before.due,if(more) before.items.size else 0)
                mutable.value=before.copy(items=(if(more) before.items else emptyList())+page.items,recipients=page.recipients,more=page.hasMore,loading=false)
            } catch(e: CancellationException) { throw e } catch(e: Exception) { mutable.value=before.copy(loading=false,error=e.message ?: tr("Не удалось загрузить поручения")) }
        }
    }
    class Factory(private val repository: TaskRepository): ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST") override fun <T: ViewModel> create(modelClass: Class<T>): T = DelegationsViewModel(repository) as T
    }
}
@Composable fun DelegationFilter(label: String, value: String, choices: Map<String,String>, onSelect: (String)->Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick={open=true}) { Text("${label}: ${choices[value] ?: value}") }
        DropdownMenu(expanded=open,onDismissRequest={open=false}) { choices.forEach { (key,text) -> DropdownMenuItem(text={Text(text)},onClick={open=false;onSelect(key)}) } }
    }
}
@Composable fun DelegationsScreen(vm: DelegationsViewModel) {
    val context=LocalContext.current
    val s by vm.state.collectAsState()
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        var showFilters by rememberSaveable { mutableStateOf(false) }
        if(showFilters) AlertDialog(onDismissRequest={showFilters=false},title={Text(tr("Фильтры поручений"))},text={Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            DelegationFilter(tr("Исполнитель"),s.assignee, linkedMapOf("" to tr("Все исполнители")) + s.recipients.filter { it.key.isNotBlank() }.associate { it.key to (it.name.ifBlank { it.email } + if(it.name.isNotBlank() && it.email.isNotBlank()) " · ${it.email}" else "") }) { vm.filters(assignee=it) }
            DelegationFilter(tr("Статус"),s.status,linkedMapOf("" to tr("Все статусы"))+delegationStatuses) { vm.filters(status=it) }
            DelegationFilter(tr("Срок"),s.due,linkedMapOf("" to tr("Все сроки"),"overdue" to tr("Просрочено"),"none" to tr("Без срока"))) { vm.filters(due=it) }
        }},confirmButton={TextButton(onClick={showFilters=false}) {Text(tr("Готово"))}},dismissButton={TextButton(onClick={vm.filters(assignee="",status="",due="")}) {Text(tr("Сбросить"))}})
        s.error?.let {Text(it,color=MaterialTheme.colorScheme.error)}
        if(s.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
        LazyColumn(modifier=Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(8.dp)) {
            items(s.items,key={it.id}) { d ->
                DelegationCard(d, onOpen = {
                    context.startActivity(DelegationDetailActivity.intent(context, d.id))
                })
            }
            if(s.items.isEmpty() && !s.loading) item {Text(tr("Поручения не найдены"), Modifier.padding(top = 24.dp))}
            if(s.more) item {TextButton(onClick={vm.load(more=true)},enabled=!s.loading) {Text(tr("Загрузить ещё"))}}
        }
        FullTextSearchField(
            query = s.query,
            onQueryChange = { vm.filters(query = it) },
            placeholder = tr("Поиск по поручениям"),
            modifier = Modifier.imePadding(),
            extraActions = {
                val filtered = s.assignee.isNotBlank() || s.status.isNotBlank() || s.due.isNotBlank()
                IconButton(onClick = { showFilters = true }) {
                    BadgedBox(badge = { if (filtered) Badge() }) {
                        Icon(Icons.Default.FilterList,
                            contentDescription = if (filtered) tr("Фильтры · применены") else tr("Фильтры"),
                            tint = if (filtered) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            },
        )
    }
}
@Composable
private fun DelegationCard(delegation: DelegationDto, onOpen: () -> Unit) {
    val due = remember(delegation.dueAt) {
        delegation.dueAt?.let { runCatching { OffsetDateTime.parse(it) }.getOrNull() }
    }
    val overdue = delegation.status !in setOf("COMPLETED", "CANCELLED") &&
        due?.toInstant()?.isBefore(java.time.Instant.now()) == true
    val accent = when (delegation.status) {
        "COMPLETED" -> MaterialTheme.colorScheme.primary
        "CANCELLED" -> MaterialTheme.colorScheme.onSurfaceVariant
        else -> MaterialTheme.colorScheme.secondary
    }
    Card(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.45f)),
    ) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 11.dp),
            verticalAlignment = Alignment.Top) {
            Icon(Icons.Default.AssignmentInd, contentDescription = null, tint = accent,
                modifier = Modifier.padding(top = 2.dp, end = 12.dp))
            Column(Modifier.weight(1f)) {
                Text(delegation.title, style = MaterialTheme.typography.titleMedium,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(delegation.assigneeName.ifBlank {
                    delegation.assigneeEmail.ifBlank { tr("Исполнитель не определён") }
                }, modifier = Modifier.padding(top = 3.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                Surface(color = accent.copy(alpha = 0.16f), contentColor = accent,
                    shape = RoundedCornerShape(12.dp), modifier = Modifier.padding(top = 6.dp)) {
                    Text(delegationStatuses[delegation.status] ?: delegation.status,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                        style = MaterialTheme.typography.labelMedium)
                }
                val deadline = due?.atZoneSameInstant(ZoneId.systemDefault())?.format(dateTimeFormatter())
                    ?: delegation.dueAt
                Text(deadline?.let { tr("Срок: {0}", it) } ?: tr("Без срока"),
                    modifier = Modifier.padding(top = 6.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = if (overdue) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                if (overdue) Text(tr("Просрочено"), modifier = Modifier.padding(top = 3.dp),
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.error)
            }
        }
    }
}
