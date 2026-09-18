package net.muratov.assistant.ui

import android.annotation.SuppressLint
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import androidx.window.WindowSdkExtensions
import androidx.window.embedding.ActivityEmbeddingController
import androidx.window.embedding.ActivityFilter
import androidx.window.embedding.EmbeddingConfiguration
import androidx.window.embedding.RuleController
import androidx.window.embedding.SplitAttributes
import androidx.window.embedding.SplitController
import androidx.window.embedding.SplitPairFilter
import androidx.window.embedding.SplitPairRule
import androidx.window.embedding.SplitPlaceholderRule
import androidx.window.embedding.SplitRule
import androidx.window.layout.FoldingFeature
import net.muratov.assistant.ChatActivity
import net.muratov.assistant.ConversationThreadDetailActivity
import net.muratov.assistant.DetailPlaceholderActivity
import net.muratov.assistant.EventDetailActivity
import net.muratov.assistant.MainActivity
import net.muratov.assistant.MeetingContextActivity
import net.muratov.assistant.MeetingResultDetailActivity
import net.muratov.assistant.TaskDetailActivity

/** Keep the existing detail activities and back stack on both displays of a foldable. */
object AdaptiveActivityLayout {
    // Window 1.5.1 lint does not recognize the guards with this Kotlin/AGP version.
    // Extension-only APIs below are explicitly guarded at runtime.
    @SuppressLint("RequiresWindowSdk")
    fun install(context: Context, configured: Boolean) {
        val rules = RuleController.getInstance(context)
        if (!configured) {
            rules.setRules(emptySet())
            return
        }
        val main = ComponentName(context, MainActivity::class.java)
        val attributes = SplitAttributes.Builder()
            .setSplitType(SplitAttributes.SplitType.SPLIT_TYPE_EQUAL)
            .build()
        val details = listOf(
            TaskDetailActivity::class.java, MeetingContextActivity::class.java,
            MeetingResultDetailActivity::class.java, ConversationThreadDetailActivity::class.java,
            EventDetailActivity::class.java, ChatActivity::class.java,
        )
        val pair = SplitPairRule.Builder(details.map {
            SplitPairFilter(main, ComponentName(context, it), null)
        }.toSet())
            .setDefaultSplitAttributes(attributes)
            .setMinWidthDp(720)
            .setMinSmallestWidthDp(600)
            .setFinishPrimaryWithSecondary(SplitRule.FinishBehavior.NEVER)
            .setFinishSecondaryWithPrimary(SplitRule.FinishBehavior.ALWAYS)
            .setClearTop(true)
            .build()
        val placeholder = SplitPlaceholderRule.Builder(
            setOf(ActivityFilter(main, null)),
            Intent(context, DetailPlaceholderActivity::class.java),
        )
            .setDefaultSplitAttributes(attributes)
            .setMinWidthDp(720)
            .setMinSmallestWidthDp(600)
            .setSticky(false)
            .setFinishPrimaryWithPlaceholder(SplitRule.FinishBehavior.ADJACENT)
            .build()
        rules.setRules(setOf(pair, placeholder))

        if (WindowSdkExtensions.getInstance().extensionVersion >= 2) {
            // WindowManager invokes this again on folding, unfolding, resizing and rotation.
            SplitController.getInstance(context).setSplitAttributesCalculator { params ->
                if (!params.areDefaultConstraintsSatisfied) {
                    SplitAttributes.Builder()
                        .setSplitType(SplitAttributes.SplitType.SPLIT_TYPE_EXPAND).build()
                } else {
                    val fold = params.parentWindowLayoutInfo.displayFeatures
                        .filterIsInstance<FoldingFeature>().firstOrNull { it.isSeparating }
                    SplitAttributes.Builder()
                        .setSplitType(
                            if (fold != null) SplitAttributes.SplitType.SPLIT_TYPE_HINGE
                            else SplitAttributes.SplitType.SPLIT_TYPE_EQUAL,
                        )
                        .setLayoutDirection(
                            if (fold?.orientation == FoldingFeature.Orientation.HORIZONTAL)
                                SplitAttributes.LayoutDirection.TOP_TO_BOTTOM
                            else SplitAttributes.LayoutDirection.LOCALE,
                        )
                        .build()
                }
            }
        }
        if (WindowSdkExtensions.getInstance().extensionVersion >= 8) {
            ActivityEmbeddingController.getInstance(context).setEmbeddingConfiguration(
                EmbeddingConfiguration.Builder().setAutoSaveEmbeddingState(true).build(),
            )
        }
    }
}
