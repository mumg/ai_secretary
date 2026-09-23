package net.muratov.assistant

import net.muratov.assistant.i18n.tr

import android.Manifest
import android.app.Activity
import android.app.DatePickerDialog
import android.app.TimePickerDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.security.KeyChain
import android.speech.RecognizerIntent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.border
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Sort
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Alarm
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Circle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SyncAlt
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import net.muratov.assistant.setup.SetupWizard
import net.muratov.assistant.updates.UpdatePrompt
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import net.muratov.assistant.data.local.TaskEntity
import net.muratov.assistant.data.remote.ConversationThreadDto
import net.muratov.assistant.data.remote.MeetingDto
import net.muratov.assistant.data.remote.MeetingResultDto
import net.muratov.assistant.data.remote.ComponentStatusDto
import net.muratov.assistant.ui.ImproverTheme
import net.muratov.assistant.ui.RejectTaskButton
import net.muratov.assistant.ui.TaskActionIconButton
import net.muratov.assistant.ui.ArchiveSwitch
import net.muratov.assistant.ui.MeetingResultsViewModel
import net.muratov.assistant.ui.MeetingsViewModel
import net.muratov.assistant.ui.TaskViewModel
import net.muratov.assistant.ui.ThreadsViewModel
import net.muratov.assistant.ui.SystemStatusViewModel
import net.muratov.assistant.ui.SystemStatusUiState
import net.muratov.assistant.ui.parseMessageLinks
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

class MainActivity : net.muratov.assistant.i18n.LocalizedActivity() {
    private val settingsLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK) recreate()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val application = application as ImproverApplication
        net.muratov.assistant.ui.AdaptiveActivityLayout.install(this, application.container.settings.isConfigured)
        if (application.container.settings.isConfigured && Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 10)
        }
        setContent {
            ImproverTheme {
                var showSetup by rememberSaveable { mutableStateOf(!application.container.settings.isConfigured) }
                UpdatePrompt(application.container.updates)
                if (showSetup) {
                    SetupWizard(application.container.settings, onComplete = {
                        application.container.realtime.connectionSettingsChanged()
                        showSetup = false
                        recreate()
                    }, onCancel = if (application.container.settings.isConfigured) ({ showSetup = false }) else null)
                    return@ImproverTheme
                }
                val taskViewModel: TaskViewModel = viewModel(
                    factory = TaskViewModel.Factory(application.container.repository),
                )
                val threadsViewModel: ThreadsViewModel = viewModel(
                    factory = ThreadsViewModel.Factory(application.container.repository),
                )
                val meetingsViewModel: MeetingsViewModel = viewModel(
                    factory = MeetingsViewModel.Factory(application.container.repository),
                )
                val meetingResultsViewModel: MeetingResultsViewModel = viewModel(
                    factory = MeetingResultsViewModel.Factory(application.container.repository),
                )
                val systemStatusViewModel: SystemStatusViewModel = viewModel(
                    factory = SystemStatusViewModel.Factory(application.container.repository),
                )
                ImproverScreen(
                    viewModel = taskViewModel,
                    threadsViewModel = threadsViewModel,
                    meetingsViewModel = meetingsViewModel,
                    meetingResultsViewModel = meetingResultsViewModel,
                    systemStatusViewModel = systemStatusViewModel,
                    onSettings = { settingsLauncher.launch(Intent(this, SettingsActivity::class.java)) },
                    onOpenTask = { taskId ->
                        startActivity(TaskDetailActivity.intent(this, taskId))
                    },
                    onOpenChat = { startActivity(Intent(this, ChatActivity::class.java)) },
                    onOpenMeeting = { meetingId ->
                        startActivity(MeetingContextActivity.intent(this, meetingId))
                    },
                    onOpenMeetingResult = { resultId ->
                        startActivity(MeetingResultDetailActivity.intent(this, resultId))
                    },
                    onOpenThread = { threadId ->
                        startActivity(ConversationThreadDetailActivity.intent(this, threadId))
                    },

                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun ImproverScreen(
    viewModel: TaskViewModel,
    threadsViewModel: ThreadsViewModel,
    meetingsViewModel: MeetingsViewModel,
    meetingResultsViewModel: MeetingResultsViewModel,
    systemStatusViewModel: SystemStatusViewModel,
    onSettings: () -> Unit,
    onOpenTask: (String) -> Unit,
    onOpenChat: () -> Unit,
    onOpenMeeting: (String) -> Unit,
    onOpenMeetingResult: (String) -> Unit,
    onOpenThread: (String) -> Unit,
) {
    val tasks by viewModel.tasks.collectAsState()
    val archivedTasks by viewModel.archivedTasks.collectAsState()
    val loading by viewModel.loading.collectAsState()
    val taskRefreshing by viewModel.refreshing.collectAsState()
    val voiceProcessing by viewModel.voiceProcessing.collectAsState()
    val error by viewModel.error.collectAsState()
    val todayMeetings by viewModel.todayMeetings.collectAsState()
    val taskSearchQuery by viewModel.searchQuery.collectAsState()
    val taskSearchResults by viewModel.searchResults.collectAsState()
    val taskSearchLoading by viewModel.searchLoading.collectAsState()
    val taskSearchError by viewModel.searchError.collectAsState()
    val systemStatus by systemStatusViewModel.state.collectAsState()
    val realtimeConnected by net.muratov.assistant.notifications.RealtimeState.connected.collectAsState()
    val currentTime = rememberMinuteClock()
    val visibleTodayMeetings = remember(todayMeetings, currentTime) {
        todayMeetings.filter { meetingIsUpcoming(it, currentTime) }
    }
    val context = LocalContext.current
    val delegationViewModel: net.muratov.assistant.ui.DelegationsViewModel = viewModel(factory = net.muratov.assistant.ui.DelegationsViewModel.Factory((context.applicationContext as ImproverApplication).container.repository))
    val delegationState by delegationViewModel.state.collectAsState()
    var showCreate by remember { mutableStateOf(false) }
    var reminderTask by remember { mutableStateOf<TaskEntity?>(null) }
    var sortByDue by rememberSaveable { mutableStateOf(false) }
    var taskArchive by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(taskArchive) { viewModel.setArchiveMode(taskArchive) }
    var voiceError by remember { mutableStateOf<String?>(null) }
    val pagerState = rememberPagerState(pageCount = { HomeTab.entries.size })
    val pagerScope = rememberCoroutineScope()
    val selectedTab = HomeTab.entries[pagerState.currentPage]
    val selectTab: (HomeTab) -> Unit = { tab ->
        pagerScope.launch { pagerState.animateScrollToPage(tab.ordinal) }
    }
    val speechIntent = remember {
        Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
            )
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, net.muratov.assistant.i18n.Language.locale.toLanguageTag())
            putExtra(
                RecognizerIntent.EXTRA_PROMPT,
                tr("Назовите задачу, приоритет и срок выполнения"),
            )
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
        }
    }
    val speechLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val recognized = result.data
                ?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
                ?.firstOrNull()
                ?.trim()
            if (recognized.isNullOrEmpty()) {
                voiceError = tr("Речь не распознана")
            } else {
                voiceError = null
                viewModel.createFromVoice(recognized)
            }
        }
    }
    val launchSpeech = {
        voiceError = null
        runCatching { speechLauncher.launch(speechIntent) }
            .onFailure { voiceError = tr("Распознавание речи недоступно на устройстве") }
        Unit
    }
    val microphonePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            launchSpeech()
        } else {
            voiceError = tr("Разрешите доступ к микрофону для голосового добавления задачи")
        }
    }
    val startVoiceCapture = {
        if (
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.RECORD_AUDIO,
            ) == PackageManager.PERMISSION_GRANTED
        ) {
            launchSpeech()
        } else {
            microphonePermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
    val searchedTasks = if (taskSearchQuery.isBlank()) {
        if (taskArchive) archivedTasks else tasks
    } else taskSearchResults.orEmpty()
    val displayedTasks = remember(searchedTasks, sortByDue, taskArchive) {
        if (taskArchive || !sortByDue) {
            searchedTasks
        } else {
            val priorityWeight = mapOf("CRITICAL" to 4, "HIGH" to 3, "NORMAL" to 2, "LOW" to 1)
            searchedTasks.sortedWith(
                compareByDescending<TaskEntity> { priorityWeight[it.priority] ?: 0 }
                    .thenBy { it.dueAt ?: "9999" },
            )
        }
    }

    Scaffold(
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Column {
                            Text(
                                when (selectedTab) {
                                    HomeTab.DELEGATIONS -> tr("Поручения")
                                    HomeTab.TASKS -> if (taskArchive) tr("Архив задач") else tr("План на сегодня")
                                    HomeTab.MEETINGS -> tr("Встречи")
                                    HomeTab.RESULTS -> tr("Результаты встреч")
                                    HomeTab.THREADS -> tr("Резюме переписок")
                                },
                            )
                            val subtitle = when (selectedTab) {
                                HomeTab.DELEGATIONS -> null
                                HomeTab.TASKS -> if (taskArchive) null else if (sortByDue) tr("По приоритету и сроку") else tr("По рейтингу")
                                HomeTab.MEETINGS -> tr("Предстоящие по времени")
                                HomeTab.RESULTS, HomeTab.THREADS -> tr("Сначала новые")
                            }
                            subtitle?.let {
                                Text(
                                    it,
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    },
                    actions = {
                        ServerConnectionIndicator(
                            connected = if (realtimeConnected) true else systemStatus.serverReachable,
                            onClick = onSettings,
                        )
                        SystemHealthIndicator(
                            status = systemStatus.snapshot?.overallStatus
                                ?: "UNKNOWN",
                            onClick = { context.startActivity(Intent(context, SystemStatusActivity::class.java)) },
                        )
                    },
                )
                androidx.compose.material3.ScrollableTabRow(selectedTabIndex = selectedTab.ordinal, edgePadding = 0.dp) {
                    Tab(
                        selected = selectedTab == HomeTab.TASKS,
                        onClick = { selectTab(HomeTab.TASKS) },
                        modifier = Modifier.height(48.dp),
                        content = { HomeTabLabel(tr("Задачи")) },
                    )
                    Tab(selected = selectedTab == HomeTab.DELEGATIONS, onClick = { selectTab(HomeTab.DELEGATIONS) }, content = { HomeTabLabel(tr("Поручения")) })
                    Tab(
                        selected = selectedTab == HomeTab.MEETINGS,
                        onClick = { selectTab(HomeTab.MEETINGS) },
                        modifier = Modifier.height(48.dp),
                        content = { HomeTabLabel(tr("Встречи")) },
                    )
                    Tab(
                        selected = selectedTab == HomeTab.RESULTS,
                        onClick = { selectTab(HomeTab.RESULTS) },
                        modifier = Modifier.height(48.dp),
                        content = { HomeTabLabel(tr("Итоги")) },
                    )
                    Tab(
                        selected = selectedTab == HomeTab.THREADS,
                        onClick = { selectTab(HomeTab.THREADS) },
                        modifier = Modifier.height(48.dp),
                        content = { HomeTabLabel(tr("Переписки")) },
                    )

                }
            }
        },
        bottomBar = {
            if (selectedTab == HomeTab.TASKS && !taskArchive) {
                RightThumbActionBar(
                    sortByDue = sortByDue,
                    voiceProcessing = voiceProcessing,
                    onSettings = onSettings,
                    onSort = { sortByDue = !sortByDue },
                    onChat = onOpenChat,
                    onAdd = { showCreate = true },
                    onVoiceAdd = startVoiceCapture,
                )
            } else if (selectedTab == HomeTab.MEETINGS) {
                SecondaryActionBar(
                    onSettings = onSettings,
                    onChat = onOpenChat,
                )
            } else if (selectedTab == HomeTab.RESULTS) {
                SecondaryActionBar(
                    onSettings = onSettings,
                    onChat = onOpenChat,
                )
            } else if (selectedTab == HomeTab.THREADS) {
                SecondaryActionBar(
                    onSettings = onSettings,
                    onChat = onOpenChat,
                )
            } else {
                SecondaryActionBar(
                    onSettings = onSettings,
                    onChat = onOpenChat,
                )
            }
        },
    ) { padding ->
        val meetingsState by meetingsViewModel.state.collectAsState()
        val meetingResultsState by meetingResultsViewModel.state.collectAsState()
        val threadsState by threadsViewModel.state.collectAsState()
        HorizontalPager(
            state = pagerState,
            modifier = Modifier.fillMaxSize().padding(padding),
            key = { HomeTab.entries[it] },
        ) { page ->
            val pageTab = HomeTab.entries[page]
            val refreshing = when (pageTab) {
                HomeTab.DELEGATIONS -> delegationState.loading
                HomeTab.TASKS -> taskRefreshing
                HomeTab.MEETINGS -> meetingsState.refreshing
                HomeTab.RESULTS -> meetingResultsState.refreshing
                HomeTab.THREADS -> threadsState.refreshing
            }
            PullToRefreshBox(
                isRefreshing = refreshing,
                onRefresh = {
                    when (pageTab) {
                        HomeTab.DELEGATIONS -> delegationViewModel.load()
                        HomeTab.TASKS -> viewModel.refreshFromPull()
                        HomeTab.MEETINGS -> meetingsViewModel.refresh(fromPull = true)
                        HomeTab.RESULTS -> meetingResultsViewModel.refresh(fromPull = true)
                        HomeTab.THREADS -> threadsViewModel.refresh(fromPull = true)
                    }
                },
                modifier = Modifier.fillMaxSize(),
            ) {
                when (pageTab) {
                    HomeTab.DELEGATIONS -> net.muratov.assistant.ui.DelegationsScreen(delegationViewModel)
                    HomeTab.TASKS -> Column(
                        Modifier.fillMaxSize().padding(horizontal = 16.dp),
                    ) {
                ArchiveSwitch(archive = taskArchive, onSelect = { taskArchive = it })
                if (error != null) Text(error!!, color = MaterialTheme.colorScheme.error)
                if (taskSearchError != null) {
                    Text(taskSearchError!!, color = MaterialTheme.colorScheme.error)
                }
                if (voiceProcessing) {
                    Text(
                        tr("Qwen оформляет надиктованную задачу…"),
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
                voiceError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                if (
                    taskSearchQuery.isBlank() && searchedTasks.isEmpty() &&
                    (taskArchive || visibleTodayMeetings.isEmpty()) && !loading
                ) {
                    Text(if (taskArchive) tr("В архиве пока нет задач") else tr("На сегодня ничего не запланировано"), Modifier.padding(top = 24.dp))
                } else if (
                    taskSearchQuery.isNotBlank() && displayedTasks.isEmpty() &&
                    !taskSearchLoading
                ) {
                    Text(tr("По запросу задачи не найдены"), Modifier.padding(top = 24.dp))
                }
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (!taskArchive && taskSearchQuery.isBlank() && visibleTodayMeetings.isNotEmpty()) {
                        item {
                            Text(
                                tr("Встречи сегодня"),
                                modifier = Modifier.padding(top = 8.dp, bottom = 2.dp),
                                style = MaterialTheme.typography.titleMedium,
                            )
                        }
                        items(visibleTodayMeetings, key = { "today-${it.id}" }) { meeting ->
                            MeetingCard(
                                meeting = meeting,
                                compact = true,
                                onOpen = {
                                    onOpenMeeting(meeting.id)
                                },
                            )
                        }
                        item {
                            Text(
                                tr("Задачи"),
                                modifier = Modifier.padding(top = 8.dp, bottom = 2.dp),
                                style = MaterialTheme.typography.titleMedium,
                            )
                        }
                    }
                    items(displayedTasks, key = TaskEntity::id) { task ->
                        TaskCard(
                            task = task,
                            onOpen = { onOpenTask(task.id) },
                            onComplete = { viewModel.complete(task.id) },
                            onConfirm = { viewModel.confirm(task.id) },
                            onReject = { viewModel.reject(task.id) },
                            onAddReminder = { reminderTask = task },
                        )
                    }
                }
                FullTextSearchField(
                    query = taskSearchQuery,
                    onQueryChange = viewModel::setSearchQuery,
                    placeholder = tr("Поиск по задачам"),
                )
            }
                    HomeTab.MEETINGS -> MeetingList(
                        viewModel = meetingsViewModel,
                        onOpenMeeting = onOpenMeeting,
                        modifier = Modifier.fillMaxSize(),
                    )
                    HomeTab.RESULTS -> MeetingResultList(
                        viewModel = meetingResultsViewModel,
                        onOpenResult = onOpenMeetingResult,
                        modifier = Modifier.fillMaxSize(),
                    )
                    HomeTab.THREADS -> ConversationThreadList(
                        viewModel = threadsViewModel,
                        onOpenThread = onOpenThread,
                        modifier = Modifier.fillMaxSize(),
                    )

                }
            }
        }
    }

    if (showCreate) {
        CreateTaskDialog(
            onDismiss = { showCreate = false },
            onCreate = { title, priority, due ->
                viewModel.create(title, priority, due)
                showCreate = false
            },
        )
    }
    reminderTask?.let { task ->
        ReminderDialog(
            taskTitle = task.title,
            onDismiss = { reminderTask = null },
            onSave = { remindAt ->
                viewModel.addReminder(task.id, remindAt)
                reminderTask = null
            },
        )
    }
}

@Composable
private fun HomeTabLabel(text: String) {
    Text(
        text = text,
        maxLines = 1,
        softWrap = false,
        overflow = TextOverflow.Clip,
        fontSize = 12.sp,
    )
}

private enum class HomeTab {
    TASKS,
    DELEGATIONS,
    MEETINGS,
    RESULTS,
    THREADS,
}

@Composable
private fun ServerConnectionIndicator(connected: Boolean?, onClick: () -> Unit) {
    val description = when (connected) {
        true -> tr("Работает")
        false -> tr("Нет соединения")
        null -> tr("Проверяем сервер…")
    }
    IconButton(onClick = onClick) {
        Icon(
            imageVector = Icons.Default.SyncAlt,
            contentDescription = tr("Сервер") + ": " + description,
            tint = if (connected == null) MaterialTheme.colorScheme.onSurfaceVariant else overallStatusColor(if (connected) "OK" else "ERROR"),
            modifier = Modifier.size(22.dp),
        )
    }
}

@Composable
private fun SystemHealthIndicator(status: String, onClick: () -> Unit) {
    IconButton(onClick = onClick) {
        Icon(
            imageVector = Icons.Default.Circle,
            contentDescription = tr("Состояние системы: {0}" , statusLabel(status)),
            tint = if (status == "UNKNOWN") MaterialTheme.colorScheme.onSurfaceVariant else overallStatusColor(status),
            modifier = Modifier.size(22.dp),
        )
    }
}

@Composable
internal fun SystemStatusTable(state: SystemStatusUiState, modifier: Modifier = Modifier) {
    Column(modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
        state.error?.let {
            Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(8.dp))
        }
        Surface(
            color = MaterialTheme.colorScheme.surfaceVariant,
            shape = RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp),
        ) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 9.dp)) {
                Text(tr("Компонент"), style = MaterialTheme.typography.labelMedium, modifier = Modifier.weight(1.05f))
                Text(tr("Состояние"), style = MaterialTheme.typography.labelMedium, modifier = Modifier.weight(0.8f))
                Text(tr("Данные"), style = MaterialTheme.typography.labelMedium, modifier = Modifier.weight(1.25f))
            }
        }
        LazyColumn(modifier = Modifier.weight(1f)) {
            val components = state.snapshot?.components.orEmpty()
            items(components, key = ComponentStatusDto::id) { component ->
                ComponentStatusRow(component)
            }
            if (components.isEmpty() && !state.loading) {
                item {
                    Text(
                        tr("Нет данных о компонентах"),
                        modifier = Modifier.padding(16.dp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun ComponentStatusRow(component: ComponentStatusDto) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
            .padding(horizontal = 10.dp, vertical = 10.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Column(Modifier.weight(1.05f).padding(end = 6.dp)) {
            Text(when (component.id) { "processing" -> tr("Обработка"); "ollama-semaphore" -> tr("Семафор LLM"); "worker-main" -> tr("Фоновая обработка"); else -> component.label }, style = MaterialTheme.typography.bodyMedium)
            Text(
                componentTypeLabel(component.componentType),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                formatStatusTime(component.observedAt),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Row(
            Modifier.weight(0.8f).padding(end = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                Icons.Default.Circle,
                contentDescription = null,
                tint = statusColor(component.status),
                modifier = Modifier.size(10.dp),
            )
            Spacer(Modifier.width(4.dp))
            Text(statusLabel(component.status), style = MaterialTheme.typography.labelMedium)
        }
        Column(Modifier.weight(1.25f)) {
            component.metrics.toSortedMap().forEach { (key, value) ->
                Text(
                    "${metricLabel(key)}: ${formatMetric(value)}",
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            component.message?.let {
                Text(
                    tr(it),
                    style = MaterialTheme.typography.labelSmall,
                    color = if (component.status == "ERROR") {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }
        }
    }
}

private fun statusLabel(status: String): String = when (status) {
    "OK" -> tr("Норма")
    "BUSY" -> tr("Занят")
    "DEGRADED" -> tr("Снижен")
    "ERROR" -> tr("Ошибка")
    "STALE" -> tr("Нет связи")
    "DISABLED" -> tr("Отключён")
    else -> tr("Неизвестно")
}

private fun statusColor(status: String): Color = when (status) {
    "OK" -> Color(0xFF2E7D32)
    "BUSY" -> Color(0xFF1565C0)
    "DEGRADED", "STALE", "UNKNOWN" -> Color(0xFFF9A825)
    "ERROR" -> Color(0xFFC62828)
    else -> Color(0xFF757575)
}

private fun overallStatusColor(status: String): Color = when (status) {
    "OK", "BUSY" -> Color(0xFF2E7D32)
    else -> statusColor(status)
}

private fun componentTypeLabel(type: String): String = when (type) {
    "event_loader" -> tr("Загрузчик событий")
    "external_loader" -> tr("Внешний загрузчик")
    "llm" -> tr("Языковая модель")
    "processing" -> tr("Очереди")
    "semaphore" -> tr("Семафор")
    "worker" -> tr("Фоновый процесс")
    else -> type.replace('_', ' ')
}

private fun metricLabel(key: String): String = mapOf(
    "capacity" to tr("лимит"),
    "in_use" to tr("занято"),
    "waiting" to tr("ожидает"),
    "interactive_ready" to tr("чатов готово"),
    "latency_ms" to tr("задержка, мс"),
    "model_count" to tr("моделей"),
    "poll_interval_seconds" to tr("интервал, с"),
    "seconds_since_sync" to tr("с последней загрузки, с"),
    "events_last_cycle" to tr("событий за цикл"),
    "events_loaded" to tr("загружено событий"),
    "candidates" to tr("найдено"),
    "tasks_sent" to tr("отправлено задач"),
    "duration_seconds" to tr("длительность, с"),
    "consecutive_errors" to tr("ошибок подряд"),
    "seconds_until_next_poll" to tr("до следующей загрузки, с"),
    "events_pending" to tr("событий в очереди"),
    "events_processing" to tr("событий в работе"),
    "events_failed" to tr("ошибок событий"),
    "events_retry_waiting" to tr("повторных попыток"),
    "chat_pending" to tr("чатов в очереди"),
    "chat_processing" to tr("чатов в работе"),
    "chat_failed" to tr("ошибок чата"),
    "contexts_pending" to tr("контекстов в очереди"),
    "contexts_processing" to tr("контекстов в работе"),
    "contexts_failed" to tr("ошибок контекста"),
    "tasks_active" to tr("активных задач"),
    "stuck" to tr("зависло"),
)[key] ?: key.replace('_', ' ')

private fun formatMetric(value: Double): String =
    if (value % 1.0 == 0.0) value.toLong().toString() else "%.1f".format(net.muratov.assistant.i18n.Language.locale, value)

private fun formatStatusTime(value: String): String = runCatching {
    ZonedDateTime.parse(value)
        .withZoneSameInstant(ZoneId.systemDefault())
        .format(DateTimeFormatter.ofPattern("dd.MM HH:mm:ss"))
}.getOrElse { value.take(19).replace('T', ' ') }

@Composable
internal fun FullTextSearchField(
    query: String,
    onQueryChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    extraActions: (@Composable () -> Unit)? = null,
) {
    OutlinedTextField(
        value = query,
        onValueChange = onQueryChange,
        modifier = modifier.fillMaxWidth().padding(top = 8.dp, bottom = 6.dp),
        placeholder = {
            Text(
                text = placeholder,
                maxLines = 1,
                softWrap = false,
                overflow = TextOverflow.Ellipsis,
            )
        },
        leadingIcon = {
            Icon(Icons.Default.Search, contentDescription = null)
        },
        trailingIcon = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (query.isNotEmpty()) IconButton(onClick = { onQueryChange("") }) {
                    Icon(Icons.Default.Close, contentDescription = tr("Очистить поиск"))
                }
                extraActions?.invoke()
            }
        },
        singleLine = true,
        shape = RoundedCornerShape(16.dp),
    )
}

@Composable
private fun MeetingList(
    viewModel: MeetingsViewModel,
    onOpenMeeting: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsState()
    val listState = rememberLazyListState()
    val currentTime = rememberMinuteClock()
    val visibleMeetings = remember(state.items, currentTime) {
        state.items.filter { meetingIsUpcoming(it, currentTime) }
    }
    androidx.compose.runtime.LaunchedEffect(listState, viewModel) {
        snapshotFlow {
            val layout = listState.layoutInfo
            (layout.visibleItemsInfo.lastOrNull()?.index ?: -1) to layout.totalItemsCount
        }.collect { (lastVisibleIndex, _) ->
            val current = viewModel.state.value
            if (current.items.isNotEmpty() && lastVisibleIndex >= current.items.lastIndex - 4) {
                viewModel.loadNext()
            }
        }
    }
    Column(modifier = modifier.padding(horizontal = 16.dp)) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            state.error?.let { message ->
                item {
                    Text(
                        message,
                        modifier = Modifier.padding(vertical = 8.dp),
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            if (visibleMeetings.isEmpty() && !state.loading) {
                item {
                    Text(
                        if (state.query.isBlank()) {
                            tr("Предстоящих встреч нет")
                        } else {
                            tr("По запросу встречи не найдены")
                        },
                        Modifier.padding(top = 24.dp),
                    )
                }
            }
            items(visibleMeetings, key = MeetingDto::id) { meeting ->
                MeetingCard(
                    meeting = meeting,
                    onOpen = { onOpenMeeting(meeting.id) },
                )
            }
        }
        FullTextSearchField(
            query = state.query,
            onQueryChange = viewModel::setSearchQuery,
            placeholder = tr("Поиск по встречам"),
        )
    }
}

@Composable
private fun MeetingCard(
    meeting: MeetingDto,
    compact: Boolean = false,
    onOpen: () -> Unit,
) {
    val organizer = meeting.organizer?.let {
        it["name"]?.takeIf(String::isNotBlank) ?: it["address"]
    }
    Card(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF17252E)),
        border = BorderStroke(1.dp, Color(0xFF65B5D8).copy(alpha = 0.55f)),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 11.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Icon(
                Icons.Default.Event,
                contentDescription = null,
                tint = Color(0xFF65B5D8),
                modifier = Modifier.padding(top = 2.dp, end = 12.dp),
            )
            Column(Modifier.weight(1f)) {
                Text(
                    meeting.title,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = if (compact) 1 else 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    formatMeetingTime(meeting),
                    modifier = Modifier.padding(top = 3.dp),
                    style = MaterialTheme.typography.labelLarge,
                    color = Color(0xFF8DD5F2),
                )
                meeting.location?.takeIf(String::isNotBlank)?.let { location ->
                    MeetingLocation(location)
                }
                if (!compact && !organizer.isNullOrBlank()) {
                    Text(
                        tr("Организатор: {0}" , organizer),
                        modifier = Modifier.padding(top = 3.dp),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                if (meeting.status == "CANCELLED") {
                    Text(
                        tr("Отменена"),
                        modifier = Modifier.padding(top = 3.dp),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

@Composable
private fun MeetingLocation(location: String) {
    val linkColor = MaterialTheme.colorScheme.primary
    val annotatedLocation = remember(location, linkColor) {
        buildAnnotatedString {
            parseMessageLinks(location).forEach { part ->
                val url = part.url
                if (url == null) {
                    append(part.text)
                } else {
                    withLink(
                        LinkAnnotation.Url(
                            url = url,
                            styles = TextLinkStyles(
                                style = SpanStyle(
                                    color = linkColor,
                                    textDecoration = TextDecoration.Underline,
                                ),
                            ),
                        ),
                    ) {
                        append(part.text)
                    }
                }
            }
        }
    }
    Text(
        annotatedLocation,
        modifier = Modifier.padding(top = 3.dp),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
}

@Composable
private fun MeetingResultList(
    viewModel: MeetingResultsViewModel,
    onOpenResult: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsState()
    val listState = rememberLazyListState()
    LaunchedEffect(listState, viewModel) {
        snapshotFlow {
            val layout = listState.layoutInfo
            (layout.visibleItemsInfo.lastOrNull()?.index ?: -1) to layout.totalItemsCount
        }.collect { (lastVisibleIndex, _) ->
            val current = viewModel.state.value
            if (current.items.isNotEmpty() && lastVisibleIndex >= current.items.lastIndex - 4) {
                viewModel.loadNext()
            }
        }
    }
    Column(modifier = modifier.padding(horizontal = 16.dp)) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            state.error?.let { message ->
                item { Text(message, color = MaterialTheme.colorScheme.error) }
            }
            if (state.items.isEmpty() && !state.loading) {
                item {
                    Text(
                        if (state.query.isBlank()) {
                            tr("Готовых расшифровок пока нет")
                        } else {
                            tr("По запросу результаты встреч не найдены")
                        },
                        Modifier.padding(top = 24.dp),
                    )
                }
            }
            items(state.items, key = MeetingResultDto::id) { result ->
                MeetingResultCard(
                    result = result,
                    onOpen = { onOpenResult(result.id) },
                )
            }
        }
        FullTextSearchField(
            query = state.query,
            onQueryChange = viewModel::setSearchQuery,
            placeholder = tr("Поиск по итогам"),
        )
    }
}

@Composable
private fun MeetingResultCard(result: MeetingResultDto, onOpen: () -> Unit) {
    Card(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF1B2824)),
        border = BorderStroke(1.dp, Color(0xFF62C49A).copy(alpha = 0.55f)),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 11.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Icon(
                Icons.Default.Event,
                contentDescription = null,
                tint = Color(0xFF62C49A),
                modifier = Modifier.padding(top = 2.dp, end = 12.dp),
            )
            Column(Modifier.weight(1f)) {
                Text(
                    result.title,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    formatMeetingResultTime(result),
                    modifier = Modifier.padding(top = 3.dp),
                    style = MaterialTheme.typography.labelMedium,
                    color = Color(0xFF8CE0BD),
                )
                if (result.calendarMeetingId != null) {
                    Text(
                        tr("Связано с календарём"),
                        modifier = Modifier.padding(top = 3.dp),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
                Text(
                    when (result.analysisState) {
                        "COMPLETED" -> result.briefSummary.ifBlank {
                            tr("Основные договорённости не выделены")
                        }
                        "FAILED" -> tr("Анализ материалов встречи завершился ошибкой")
                        else -> tr("Qwen анализирует материалы встречи…")
                    },
                    modifier = Modifier.padding(top = 8.dp),
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 4,
                    overflow = TextOverflow.Ellipsis,
                )
                if (result.supplementCount > 0) {
                    Text(
                        tr("Резюме участников: {0}" , result.supplementCount),
                        modifier = Modifier.padding(top = 6.dp),
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFF8CE0BD),
                    )
                }
                result.meetingUrl?.let { MeetingLocation(it) }
            }
        }
    }
}

@Composable
private fun ConversationThreadList(
    viewModel: ThreadsViewModel,
    onOpenThread: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.state.collectAsState()
    val listState = rememberLazyListState()
    androidx.compose.runtime.LaunchedEffect(listState, viewModel) {
        snapshotFlow {
            val layout = listState.layoutInfo
            (layout.visibleItemsInfo.lastOrNull()?.index ?: -1) to layout.totalItemsCount
        }.collect { (lastVisibleIndex, _) ->
            val current = viewModel.state.value
            if (
                current.items.isNotEmpty() &&
                lastVisibleIndex >= current.items.lastIndex - 4
            ) {
                viewModel.loadNext()
            }
        }
    }

    Column(modifier = modifier.padding(horizontal = 16.dp)) {
        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (state.error != null) {
                item {
                    Text(
                        state.error!!,
                        modifier = Modifier.padding(vertical = 8.dp),
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            if (state.items.isEmpty() && !state.loading) {
                item {
                    Text(
                        if (state.query.isBlank()) {
                            tr("Переписки пока не найдены")
                        } else {
                            tr("По запросу переписки не найдены")
                        },
                        Modifier.padding(top = 24.dp),
                    )
                }
            }
            items(state.items, key = ConversationThreadDto::id) { thread ->
                ConversationThreadCard(
                    thread = thread,
                    onOpen = { onOpenThread(thread.id) },
                )
            }
        }
        FullTextSearchField(
            query = state.query,
            onQueryChange = viewModel::setSearchQuery,
            placeholder = tr("Поиск по перепискам"),
        )
    }
}

@Composable
private fun ConversationThreadCard(
    thread: ConversationThreadDto,
    onOpen: () -> Unit,
) {
    val participantNames = thread.participants
        .mapNotNull { it["name"]?.takeIf(String::isNotBlank) ?: it["address"] }
        .distinct()
        .take(3)
        .joinToString(", ")
    Card(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.45f)),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Text(
                thread.title ?: tr("Переписка без темы"),
                style = MaterialTheme.typography.titleMedium,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                tr("{0} • {1} сообщ." , thread.sourceLabel, thread.eventCount),
                modifier = Modifier.padding(top = 3.dp),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            if (participantNames.isNotBlank()) {
                Text(
                    participantNames,
                    modifier = Modifier.padding(top = 3.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Text(
                thread.summary ?: tr("Резюме готовится после анализа Qwen…"),
                modifier = Modifier.padding(top = 8.dp),
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 6,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                tr("Обновлено: {0}" , formatThreadTimestamp(thread.lastEventAt)),
                modifier = Modifier.padding(top = 8.dp),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private val threadTimestampFormatter get() = net.muratov.assistant.i18n.dateTimeFormatter()
private val meetingDateFormatter get() = net.muratov.assistant.i18n.dateFormatter()
private val meetingTimeFormatter = DateTimeFormatter.ofPattern("HH:mm")

@Composable
private fun rememberMinuteClock(): ZonedDateTime {
    var current by remember { mutableStateOf(ZonedDateTime.now()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(60_000)
            current = ZonedDateTime.now()
        }
    }
    return current
}

private fun meetingIsUpcoming(meeting: MeetingDto, currentTime: ZonedDateTime): Boolean =
    runCatching {
        ZonedDateTime.parse(meeting.endsAt).isAfter(currentTime)
    }.getOrDefault(true)

private fun formatThreadTimestamp(value: String): String = runCatching {
    ZonedDateTime.parse(value)
        .withZoneSameInstant(ZoneId.systemDefault())
        .format(threadTimestampFormatter)
}.getOrDefault(value)

private fun formatMeetingTime(meeting: MeetingDto): String = runCatching {
    val start = ZonedDateTime.parse(meeting.startsAt).withZoneSameInstant(ZoneId.systemDefault())
    val end = ZonedDateTime.parse(meeting.endsAt).withZoneSameInstant(ZoneId.systemDefault())
    if (meeting.allDay) {
        tr("{0} • весь день" , start.format(meetingDateFormatter))
    } else {
        val date = start.format(meetingDateFormatter)
        val interval = "${start.format(meetingTimeFormatter)}–${end.format(meetingTimeFormatter)}"
        "${date} • ${interval}"
    }
}.getOrDefault(meeting.startsAt)

private fun formatMeetingResultTime(result: MeetingResultDto): String = formatMeetingResultPeriod(
    result.startsAt,
    result.endsAt,
    result.originType,
    result.calendarMeetingId,
)

@Composable
private fun SecondaryActionBar(
    onSettings: () -> Unit,
    onChat: () -> Unit,
) {
    Surface(tonalElevation = 8.dp, shadowElevation = 8.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 8.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onSettings, modifier = Modifier.size(52.dp)) {
                Icon(Icons.Default.Settings, contentDescription = tr("Настройки"))
            }
            IconButton(onClick = onChat, modifier = Modifier.size(52.dp)) {
                Icon(Icons.AutoMirrored.Filled.Chat, contentDescription = tr("Чат с Qwen"))
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun RightThumbActionBar(
    sortByDue: Boolean,
    voiceProcessing: Boolean,
    onSettings: () -> Unit,
    onSort: () -> Unit,
    onChat: () -> Unit,
    onAdd: () -> Unit,
    onVoiceAdd: () -> Unit,
) {
    Surface(tonalElevation = 8.dp, shadowElevation = 8.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 8.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onSettings, modifier = Modifier.size(52.dp)) {
                Icon(Icons.Default.Settings, contentDescription = tr("Настройки"))
            }
            IconButton(onClick = onSort, modifier = Modifier.size(52.dp)) {
                Icon(
                    Icons.AutoMirrored.Filled.Sort,
                    contentDescription = if (sortByDue) {
                        tr("Сортировать по рейтингу")
                    } else {
                        tr("Сортировать по приоритету и сроку")
                    },
                )
            }
            IconButton(onClick = onChat, modifier = Modifier.size(52.dp)) {
                Icon(Icons.AutoMirrored.Filled.Chat, contentDescription = tr("Чат с Qwen"))
            }
            Spacer(Modifier.width(6.dp))
            Surface(
                modifier = Modifier
                    .size(52.dp)
                    .combinedClickable(
                        enabled = !voiceProcessing,
                        onClickLabel = tr("Добавить задачу вручную"),
                        onLongClickLabel = tr("Надиктовать задачу"),
                        onClick = onAdd,
                        onLongClick = onVoiceAdd,
                    ),
                shape = CircleShape,
                color = MaterialTheme.colorScheme.primaryContainer,
                contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
                tonalElevation = 6.dp,
                shadowElevation = 6.dp,
            ) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    if (voiceProcessing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(26.dp),
                            strokeWidth = 2.dp,
                        )
                    } else {
                        Icon(Icons.Default.Add, contentDescription = tr("Добавить задачу"))
                    }
                }
            }
        }
    }
}

@Composable
private fun TaskCard(
    task: TaskEntity,
    onOpen: () -> Unit,
    onComplete: () -> Unit,
    onConfirm: () -> Unit,
    onReject: () -> Unit,
    onAddReminder: () -> Unit,
) {
    val priority = priorityVisual(task.priority)
    val rankingReasons = visibleRankingReasons(task.rankingReasons)
    Card(
        onClick = onOpen,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = priority.container,
            contentColor = MaterialTheme.colorScheme.onSurface,
        ),
        border = BorderStroke(1.dp, priority.accent.copy(alpha = 0.42f)),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(start = 14.dp, top = 10.dp, end = 6.dp, bottom = 6.dp),
        ) {
            Column(Modifier.fillMaxWidth().padding(end = 8.dp)) {
                Text(task.title, style = MaterialTheme.typography.titleMedium)
                Surface(
                    color = priority.accent.copy(alpha = 0.16f),
                    contentColor = priority.accent,
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.padding(top = 4.dp),
                ) {
                    Text(
                        priority.label,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
                if (task.status == "NEEDS_CONFIRMATION") {
                    Text(tr("Требует подтверждения"), color = MaterialTheme.colorScheme.error)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.End,
                    ) {
                        TextButton(onClick = onConfirm) { Text(tr("Добавить")) }
                        RejectTaskButton(onClick = onReject)
                    }
                } else if (task.status == "POSSIBLY_COMPLETED") {
                    Text(tr("Возможно выполнена — подтвердите"), color = priority.accent)
                } else if (task.status in setOf("COMPLETED", "CANCELLED")) {
                    Text(if (task.status == "COMPLETED") tr("Завершена") else tr("Отменена"), color = priority.accent)
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .padding(top = 4.dp, end = 4.dp),
                ) {
                    Text(
                        task.dueAt?.let { tr("Срок: {0}" , formatThreadTimestamp(it)) } ?: tr("Срок не задан"),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    if (rankingReasons.isNotBlank()) {
                        Text(
                            rankingReasons,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
                if (task.status in setOf("NEW", "IN_PROGRESS", "POSSIBLY_COMPLETED")) {
                    RejectTaskButton(onClick = onReject)
                }
                if (task.status !in setOf("COMPLETED", "CANCELLED")) TaskActionIconButton(
                    onClick = onAddReminder,
                    highlighted = task.hasActiveReminder,
                ) {
                    Icon(Icons.Default.Alarm, contentDescription = tr("Добавить напоминание"))
                }
                if (task.status !in setOf("COMPLETED", "CANCELLED")) TaskActionIconButton(
                    onClick = onComplete,
                    enabled = task.status != "NEEDS_CONFIRMATION",
                ) {
                    Icon(Icons.Default.Check, contentDescription = tr("✓ Завершить"))
                }
            }
        }
    }
}

private data class PriorityVisual(
    val label: String,
    val accent: Color,
    val container: Color,
)

private fun priorityVisual(priority: String): PriorityVisual = when (priority.uppercase()) {
    "CRITICAL" -> PriorityVisual(tr("Критический"), Color(0xFFFF7F79), Color(0xFF35181B))
    "HIGH" -> PriorityVisual(tr("Высокий"), Color(0xFFFFB45E), Color(0xFF302316))
    "LOW" -> PriorityVisual(tr("Низкий"), Color(0xFF9AA7B2), Color(0xFF1A2025))
    else -> PriorityVisual(tr("Обычный"), Color(0xFF7CB8D8), Color(0xFF14232D))
}

private fun visibleRankingReasons(value: String): String = value
    .split(" • ")
    .map(String::trim)
    .filterNot { it.startsWith(tr("приоритет:"), ignoreCase = true) }
    .filter(String::isNotBlank)
    .map { if (it.equals("срок в течение суток", ignoreCase = true)) tr("До срока меньше суток") else it }
    .joinToString(" • ")

@Composable
private fun ReminderDialog(
    taskTitle: String,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var remindAt by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(tr("Напомнить о задаче")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(taskTitle)
                DateTimeField(
                    label = tr("Дата и время напоминания"),
                    value = remindAt,
                    onValueChange = { remindAt = it },
                )
            }
        },
        confirmButton = {
            Button(
                enabled = remindAt.isNotBlank(),
                onClick = { onSave(remindAt) },
            ) { Text(tr("Сохранить")) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(tr("Отмена")) } },
    )
}

@Composable
private fun CreateTaskDialog(
    onDismiss: () -> Unit,
    onCreate: (String, String, String?) -> Unit,
) {
    var title by remember { mutableStateOf("") }
    var priority by remember { mutableStateOf("NORMAL") }
    var due by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(tr("Новая задача")) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = title,
                    onValueChange = { title = it },
                    label = { Text(tr("Название")) },
                )
                Text(tr("Приоритет"))
                listOf(
                    "LOW" to tr("Низкий"),
                    "NORMAL" to tr("Обычный"),
                    "HIGH" to tr("Высокий"),
                    "CRITICAL" to tr("Критич."),
                ).chunked(2).forEach { choices ->
                    Row {
                        choices.forEach { (value, label) ->
                            TextButton(onClick = { priority = value }) {
                                Text(if (priority == value) "• ${label}" else label)
                            }
                        }
                    }
                }
                DateTimeField(label = tr("Срок"), value = due, onValueChange = { due = it })
            }
        },
        confirmButton = {
            Button(onClick = { if (title.isNotBlank()) onCreate(title, priority, due) }) {
                Icon(Icons.Default.Check, contentDescription = null)
                Text(tr("Создать"))
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(tr("Отмена")) } },
    )
}

@Composable
fun DateTimeField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
) {
    val context = LocalContext.current
    val initial = runCatching { java.time.OffsetDateTime.parse(value).atZoneSameInstant(ZoneId.systemDefault()) }
        .getOrElse { ZonedDateTime.now() }
    Column {
        Text(if (value.isBlank()) tr("{0} не задан" , label) else "$label: ${initial.format(net.muratov.assistant.i18n.dateTimeFormatter())}")
        Row {
            Button(
                onClick = {
                    DatePickerDialog(
                        context,
                        { _, year, month, day ->
                            TimePickerDialog(
                                context,
                                { _, hour, minute ->
                                    onValueChange(
                                        ZonedDateTime.of(
                                            year,
                                            month + 1,
                                            day,
                                            hour,
                                            minute,
                                            0,
                                            0,
                                            ZoneId.systemDefault(),
                                        ).format(DateTimeFormatter.ISO_OFFSET_DATE_TIME),
                                    )
                                },
                                initial.hour,
                                initial.minute,
                                true,
                            ).show()
                        },
                        initial.year,
                        initial.monthValue - 1,
                        initial.dayOfMonth,
                    ).show()
                },
            ) { Text(tr("Выбрать")) }
            if (value.isNotBlank()) {
                TextButton(onClick = { onValueChange("") }) { Text(tr("Очистить")) }
            }
        }
    }
}
