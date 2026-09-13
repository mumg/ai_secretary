package net.muratov.assistant.data.local

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import kotlinx.coroutines.flow.Flow

@Dao
interface TaskDao {
    @Query("SELECT * FROM tasks WHERE status NOT IN ('COMPLETED', 'CANCELLED') ORDER BY rankingScore DESC, dueAt ASC")
    fun observeActive(): Flow<List<TaskEntity>>

    @Upsert
    suspend fun upsertAll(tasks: List<TaskEntity>)

    @Query("DELETE FROM tasks WHERE id NOT IN (:activeIds) AND status NOT IN ('COMPLETED', 'CANCELLED')")
    suspend fun removeMissingActive(activeIds: List<String>)

    @Query("DELETE FROM tasks WHERE status NOT IN ('COMPLETED', 'CANCELLED')")
    suspend fun removeAllActive()
}
