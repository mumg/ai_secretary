// Only successful tested builds of this exact workflow/commit may be promoted.
module.exports = async ({ github, context, core }) => {
  core.setOutput('run_id', '');
  const runs = await github.paginate(github.rest.actions.listWorkflowRuns, {
    ...context.repo, workflow_id: 'windows.yml', head_sha: context.sha,
    status: 'success', per_page: 100,
  });
  for (const run of runs) {
    if (run.id === context.runId || run.head_sha !== context.sha || run.conclusion !== 'success' ||
        !['push', 'workflow_dispatch'].includes(run.event) ||
        run.head_repository?.full_name !== `${context.repo.owner}/${context.repo.repo}` ||
        !(run.head_branch === 'main' || run.head_branch?.startsWith('windows-v'))) continue;
    const artifacts = await github.paginate(github.rest.actions.listWorkflowRunArtifacts, {
      ...context.repo, run_id: run.id, per_page: 100,
    });
    if (artifacts.some(a => a.name === 'windows-installer-tested' && !a.expired &&
        a.size_in_bytes > 0 && Date.parse(a.expires_at) > Date.now())) {
      core.setOutput('run_id', String(run.id));
      core.info(`Reusing tested Windows installer from run ${run.id}; no rebuild needed.`);
      return;
    }
  }
  core.info('No reusable tested installer for this commit; building once.');
};
