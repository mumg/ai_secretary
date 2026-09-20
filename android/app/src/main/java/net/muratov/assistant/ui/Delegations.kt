package net.muratov.assistant.ui

import androidx.compose.foundation.layout.*
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

val delegationStatuses = linkedMapOf("ASSIGNED" to "Назначено", "IN_PROGRESS" to "В работе", "IN_REVIEW" to "На проверке", "COMPLETED" to "Выполнено", "CANCELLED" to "Отменено")
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
            } catch(e: CancellationException) { throw e } catch(e: Exception) { mutable.value=before.copy(loading=false,error=e.message ?: "Не удалось загрузить поручения") }
        }
    }
    class Factory(private val repository: TaskRepository): ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST") override fun <T: ViewModel> create(modelClass: Class<T>): T = DelegationsViewModel(repository) as T
    }
}
@Composable fun DelegationFilter(label: String, value: String, choices: Map<String,String>, onSelect: (String)->Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        OutlinedButton(onClick={open=true}) { Text("$label: ${choices[value] ?: value}") }
        DropdownMenu(expanded=open,onDismissRequest={open=false}) { choices.forEach { (key,text) -> DropdownMenuItem(text={Text(text)},onClick={open=false;onSelect(key)}) } }
    }
}
@Composable fun DelegationsScreen(vm: DelegationsViewModel) {
    val context=LocalContext.current
    val s by vm.state.collectAsState()
    Column(Modifier.fillMaxSize().padding(12.dp)) {
        OutlinedTextField(value=s.query,onValueChange={vm.filters(query=it)},label={Text("Поиск по поручениям")},modifier=Modifier.fillMaxWidth(),singleLine=true)
        var showFilters by rememberSaveable { mutableStateOf(false) }
        Row {
            TextButton(onClick={showFilters=true}) { Text(if(s.assignee.isNotBlank() || s.status.isNotBlank() || s.due.isNotBlank()) "Фильтры · применены" else "Фильтры") }
            TextButton(onClick={vm.load()},enabled=!s.loading) {Text("Обновить")}
        }
        if(showFilters) AlertDialog(onDismissRequest={showFilters=false},title={Text("Фильтры поручений")},text={Column {
            DelegationFilter("Исполнитель",s.assignee, linkedMapOf("" to "Все исполнители") + s.recipients.filter { it.key.isNotBlank() }.associate { it.key to (it.name.ifBlank { it.email } + if(it.name.isNotBlank() && it.email.isNotBlank()) " · ${it.email}" else "") }) { vm.filters(assignee=it) }
            DelegationFilter("Статус",s.status,linkedMapOf("" to "Все статусы")+delegationStatuses) { vm.filters(status=it) }
            DelegationFilter("Срок",s.due,linkedMapOf("" to "Все сроки","overdue" to "Просрочено","none" to "Без срока")) { vm.filters(due=it) }
        }},confirmButton={TextButton(onClick={showFilters=false}) {Text("Готово")}},dismissButton={TextButton(onClick={vm.filters(assignee="",status="",due="")}) {Text("Сбросить")}})
        s.error?.let {Text(it,color=MaterialTheme.colorScheme.error)}
        if(s.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
        LazyColumn(verticalArrangement=Arrangement.spacedBy(8.dp)) {
            items(s.items,key={it.id}) { d ->
                Card(onClick={context.startActivity(DelegationDetailActivity.intent(context,d.id))},modifier=Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text(d.title,style=MaterialTheme.typography.titleMedium)
                        Text(d.assigneeName.ifBlank { d.assigneeEmail.ifBlank { "Исполнитель не определён" } })
                        Text("${delegationStatuses[d.status] ?: d.status} · ${d.dueAt ?: "Без срока"}")
                        if (d.dueAt != null && d.status !in listOf("COMPLETED","CANCELLED") && runCatching { java.time.OffsetDateTime.parse(d.dueAt).toInstant().isBefore(java.time.Instant.now()) }.getOrDefault(false)) Text("Просрочено",color=MaterialTheme.colorScheme.error)
                    }
                }
            }
            if(s.items.isEmpty() && !s.loading) item {Text("Поручения не найдены")}
            if(s.more) item {TextButton(onClick={vm.load(more=true)},enabled=!s.loading) {Text("Загрузить ещё")}}
        }
    }
}
@Composable fun RelationshipSettings(repository: TaskRepository) {
    var managers by rememberSaveable { mutableStateOf("") }; var reports by rememberSaveable { mutableStateOf("") }
    var ready by remember { mutableStateOf(false) }; var busy by remember { mutableStateOf(false) }; var message by remember { mutableStateOf("") }
    val scope=rememberCoroutineScope()
    LaunchedEffect(repository) {
        try {
            val data=repository.relationships()
            fun lines(p: List<EmployeeDto>): String = p.joinToString("\n") { "${it.name} | ${it.emails.joinToString(", ")}" }
            managers=lines(data.managers)
            reports=lines(data.reports)
            ready=true
        }
        catch(e: CancellationException) {throw e} catch(e: Exception) {message=e.message ?: "Не удалось загрузить сотрудников"}
    }
    Text("Сотрудники",style=MaterialTheme.typography.titleMedium)
    Text("По одному на строку: Имя | email, второй email")
    OutlinedTextField(managers,{managers=it},label={Text("Мои руководители")},enabled=ready,modifier=Modifier.fillMaxWidth())
    OutlinedTextField(reports,{reports=it},label={Text("Мои подчинённые")},enabled=ready,modifier=Modifier.fillMaxWidth())
    if(message.isNotBlank()) Text(message)
    Button(enabled=ready && !busy,onClick={scope.launch {
        busy=true
        try {
            fun parse(text: String)=text.lines().filter { it.isNotBlank() }.map { line -> val parts=line.split('|'); require(parts.size==2 && parts[0].isNotBlank()) {"Формат строки: Имя | email, второй email"}; EmployeeDto(parts[0].trim(),parts[1].split(',').map {it.trim()}.filter {it.isNotEmpty()}) }
            repository.saveRelationships(RelationshipsDto(parse(managers),parse(reports)));message="Сохранено"
        } catch(e: CancellationException) {throw e} catch(e: Exception) {message=e.message ?: "Не удалось сохранить"} finally {busy=false}
    }}) {Text("Сохранить сотрудников")}
}
