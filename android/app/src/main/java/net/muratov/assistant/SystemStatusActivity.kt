package net.muratov.assistant

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import net.muratov.assistant.i18n.LocalizedActivity
import net.muratov.assistant.i18n.tr
import net.muratov.assistant.ui.ImproverTheme
import net.muratov.assistant.ui.SystemStatusViewModel

class SystemStatusActivity : LocalizedActivity() {
    @OptIn(ExperimentalMaterial3Api::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val repository = (application as ImproverApplication).container.repository
        setContent {
            ImproverTheme {
                val model: SystemStatusViewModel = viewModel(factory = SystemStatusViewModel.Factory(repository))
                val state by model.state.collectAsState()
                Scaffold(topBar = {
                    TopAppBar(title = { Text(tr("Состояние компонентов")) }, navigationIcon = {
                        IconButton(onClick = ::finish) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = tr("Назад"))
                        }
                    })
                }) { padding ->
                    PullToRefreshBox(
                        isRefreshing = state.refreshing,
                        onRefresh = { model.refresh(fromPull = true) },
                        modifier = Modifier.fillMaxSize().padding(padding),
                    ) {
                        SystemStatusTable(state, Modifier.fillMaxSize())
                    }
                }
            }
        }
    }
}
