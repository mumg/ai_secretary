package net.muratov.assistant.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.google.gson.Gson
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import net.muratov.assistant.data.TaskRepository
import net.muratov.assistant.data.remote.EmployeeDto
import net.muratov.assistant.data.remote.RelationshipsDto
import net.muratov.assistant.i18n.tr

@Composable fun RelationshipSettings(repository: TaskRepository) {
    var saved by rememberSaveable { mutableStateOf<String?>(null) }
    val data = remember(saved) { saved?.let { Gson().fromJson(it, RelationshipsDto::class.java) } ?: RelationshipsDto() }
    var busy by remember { mutableStateOf(false) }
    var message by rememberSaveable { mutableStateOf("") }
    var group by rememberSaveable { mutableStateOf("") }
    var index by rememberSaveable { mutableIntStateOf(-1) }
    var name by rememberSaveable { mutableStateOf("") }
    var emails by rememberSaveable { mutableStateOf("") }
    var error by rememberSaveable { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    fun change(key: String, rows: List<EmployeeDto>) {
        saved = Gson().toJson(if (key == "managers") data.copy(managers = rows) else data.copy(reports = rows))
        message = ""
    }
    suspend fun load() {
        try { saved = Gson().toJson(repository.relationships()); message = "" }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { message = e.message ?: tr("Не удалось загрузить сотрудников") }
    }
    LaunchedEffect(repository) { if (saved == null) load() }
    Text(tr("Сотрудники"), style = MaterialTheme.typography.titleMedium)
    for ((key, title, rows) in listOf(Triple("managers", "Мои руководители", data.managers), Triple("reports", "Мои подчинённые", data.reports))) {
        Text(tr(title), style = MaterialTheme.typography.titleSmall)
        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
            Text(tr("Имя"), Modifier.weight(1f)); Text(tr("Email"), Modifier.weight(1.3f)); Spacer(Modifier.width(48.dp))
        }
        HorizontalDivider()
        if (rows.isEmpty()) Text(tr("Нет сотрудников"), Modifier.padding(vertical = 12.dp), style = MaterialTheme.typography.bodySmall)
        rows.forEachIndexed { i, person ->
            Row(Modifier.fillMaxWidth().clickable(enabled = !busy) {
                group = key; index = i; name = person.name; emails = person.emails.joinToString(", "); error = ""
            }.padding(vertical = 8.dp)) {
                Text(person.name, Modifier.weight(1f).padding(end = 8.dp))
                Text(person.emails.joinToString("\n"), Modifier.weight(1.3f), style = MaterialTheme.typography.bodySmall)
                IconButton(enabled = !busy, onClick = { change(key, rows.filterIndexed { j, _ -> j != i }) }) {
                    Icon(Icons.Default.Delete, tr("Удалить") + ": " + person.name)
                }
            }
            HorizontalDivider()
        }
        TextButton(enabled = saved != null && !busy && rows.size < 200, onClick = {
            group = key; index = -1; name = ""; emails = ""; error = ""
        }) { Text(tr("Добавить сотрудника")) }
    }
    if (message.isNotBlank()) Text(message)
    if (saved == null) TextButton(onClick = { scope.launch { load() } }) { Text(tr("Повторить")) }
    Button(enabled = saved != null && !busy, onClick = { scope.launch {
        busy = true
        try { repository.saveRelationships(data); message = tr("Сохранено") }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { message = e.message ?: tr("Не удалось сохранить") }
        finally { busy = false }
    } }) { Text(tr("Сохранить сотрудников")) }
    if (group.isNotEmpty()) AlertDialog(
        onDismissRequest = { group = "" },
        title = { Text(tr(if (index < 0) "Добавить сотрудника" else "Изменить сотрудника")) },
        text = { Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(name, { name = it }, label = { Text(tr("Имя")) }, singleLine = true)
            OutlinedTextField(emails, { emails = it }, label = { Text(tr("Email")) },
                supportingText = { Text(tr("Email через запятую")) }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email))
            if (error.isNotEmpty()) Text(error, color = MaterialTheme.colorScheme.error)
        } },
        dismissButton = { TextButton(onClick = { group = "" }) { Text(tr("Отмена")) } },
        confirmButton = { TextButton(onClick = {
            val addresses = emails.split(',').map { it.trim().lowercase() }
            val rows = if (group == "managers") data.managers else data.reports
            val existing = rows.filterIndexed { i, _ -> i != index }.flatMap { it.emails }.map { it.lowercase() }
            when {
                name.trim().isEmpty() || name.trim().length > 200 -> error = tr("Укажите имя сотрудника")
                addresses.size > 20 || addresses.any { !android.util.Patterns.EMAIL_ADDRESS.matcher(it).matches() || it in existing } || addresses.distinct().size != addresses.size -> error = tr("Укажите корректные email без повторов")
                else -> {
                    val person = EmployeeDto(name.trim(), addresses)
                    change(group, if (index < 0) rows + person else rows.mapIndexed { i, old -> if (i == index) person else old })
                    group = ""
                }
            }
        }) { Text(tr("Сохранить")) } }
    )
}
