package net.muratov.assistant.ui

import net.muratov.assistant.i18n.tr

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp

@Composable
fun ArchiveSwitch(archive: Boolean, onSelect: (Boolean) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        ArchiveChoice(tr("Активные"), selected = !archive) { if (archive) onSelect(false) }
        ArchiveChoice(tr("Архив"), selected = archive) { if (!archive) onSelect(true) }
    }
}

@Composable
private fun ArchiveChoice(label: String, selected: Boolean, onClick: () -> Unit) {
    if (selected) Button(onClick = onClick) { Text(label) }
    else OutlinedButton(onClick = onClick) { Text(label) }
}
