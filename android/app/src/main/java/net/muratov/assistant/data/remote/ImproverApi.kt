package net.muratov.assistant.data.remote

import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming

interface ImproverApi {
    @GET("api/v1/tasks")
    suspend fun tasks(
        @Query("order") order: String = "rank",
        @Query("q") query: String? = null,
    ): List<TaskDto>

    @GET("api/v1/tasks/{id}")
    suspend fun task(@Path("id") id: String): TaskDetailDto

    @GET("api/v1/plans/today")
    suspend fun today(@Query("refresh") refresh: Boolean = false): DailyPlanDto

    @POST("api/v1/tasks")
    suspend fun createTask(@Body request: CreateTaskRequest): TaskDto

    @POST("api/v1/tasks/from-text")
    suspend fun createTaskFromText(@Body request: CreateTaskFromTextRequest): TaskDto

    @POST("api/v1/tasks/{id}/complete")
    suspend fun complete(@Path("id") id: String): TaskDto

    @POST("api/v1/tasks/{id}/confirm")
    suspend fun confirm(@Path("id") id: String): TaskDto

    @POST("api/v1/tasks/{id}/reject")
    suspend fun reject(@Path("id") id: String): TaskDto

    @POST("api/v1/tasks/{id}/reminders")
    suspend fun addReminder(
        @Path("id") id: String,
        @Body request: CreateReminderRequest,
    ): ReminderDto

    @PUT("api/v1/devices/current")
    suspend fun registerDevice(@Body request: DeviceRequest)

    @POST("api/v1/chat/query")
    suspend fun chat(@Body request: ChatQueryRequest): ChatResponseDto

    @Streaming
    @POST("api/v1/chat/stream")
    suspend fun chatStream(@Body request: ChatQueryRequest): Response<ResponseBody>

    @GET("api/v1/events/{id}")
    suspend fun event(@Path("id") id: String): EventDto

    @GET("api/v1/threads")
    suspend fun threads(
        @Query("offset") offset: Int,
        @Query("limit") limit: Int = 20,
        @Query("q") query: String? = null,
    ): ConversationThreadPageDto

    @GET("api/v1/threads/{id}")
    suspend fun thread(@Path("id") id: String): ConversationThreadDetailDto

    @GET("api/v1/meetings")
    suspend fun meetings(
        @Query("offset") offset: Int,
        @Query("limit") limit: Int = 20,
        @Query("q") query: String? = null,
    ): MeetingPageDto

    @GET("api/v1/meeting-results")
    suspend fun meetingResults(
        @Query("offset") offset: Int,
        @Query("limit") limit: Int = 20,
        @Query("q") query: String? = null,
    ): MeetingResultPageDto

    @GET("api/v1/meeting-results/{id}")
    suspend fun meetingResult(@Path("id") id: String): MeetingResultDetailDto
}
