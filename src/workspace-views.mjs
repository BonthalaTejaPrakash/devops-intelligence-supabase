import { connectWorkspace, createDemoWorkspace, emptyProject } from './workspace.mjs';

export function createWorkspaceViews(React) {
  const { createElement: h, useEffect, useState } = React;

  function useWorkspace() {
    const [connection, setConnection] = useState({ loading: true, error: '', api: null, user: null });
    const [projectState, setProjectState] = useState({ userId: null, items: [], loading: true, error: '' });
    const [dataRevision, setDataRevision] = useState(0);
    const [dataState, setDataState] = useState({
      userId: null, profile: null, pipelines: [], runs: [], reminders: [],
      integrations: [], notificationPreferences: [], notificationDeliveries: [],
      logs: [], auditLogs: [], metrics: null, loading: true, error: '',
    });
    const { api, user } = connection;
    const userId = user?.id;

    useEffect(() => {
      let active = true;
      let unsubscribe;

      async function restore() {
        let revision = 0;
        try {
          const workspace = await connectWorkspace();
          if (!active) return;

          unsubscribe = workspace.onAuthChange((event, session) => {
            if (!active || event === 'INITIAL_SESSION') return;
            revision += 1;
            setConnection({
              loading: false,
              error: '',
              api: workspace,
              user: session?.user ?? null,
            });
          });

          const currentRevision = revision;
          try {
            const verifiedUser = await workspace.currentUser();
            if (active && currentRevision === revision) {
              setConnection({ loading: false, error: '', api: workspace, user: verifiedUser });
            }
          } catch (error) {
            if (active && currentRevision === revision) {
              setConnection({ loading: false, error: error.message, api: workspace, user: null });
            }
          }
        } catch (error) {
          if (active) {
            const demo = createDemoWorkspace();
            unsubscribe = demo.onAuthChange((event, session) => {
              if (!active || event === 'INITIAL_SESSION') return;
              setConnection({
                loading: false,
                error: '',
                api: demo,
                user: session?.user ?? null,
              });
            });
            setConnection({
              loading: false,
              error: '',
              api: demo,
              user: null,
              demoMode: true,
              demoError: error.message,
            });
          }
        }
      }

      void restore();
      return () => { active = false; unsubscribe?.(); };
    }, []);

    useEffect(() => {
      if (!api || !userId) return;
      let active = true;
      setProjectState({ userId, items: [], loading: true, error: '' });
      api.listProjects().then(
        items => { if (active) setProjectState({ userId, items, loading: false, error: '' }); },
        error => { if (active) setProjectState({ userId, items: [], loading: false, error: error.message }); },
      );
      return () => { active = false; };
    }, [api, userId]);

    useEffect(() => {
      if (!api || !userId) {
        setDataState({
          userId: null, profile: null, pipelines: [], runs: [], reminders: [],
          integrations: [], notificationPreferences: [], notificationDeliveries: [],
          logs: [], auditLogs: [], metrics: null, loading: false, error: '',
        });
        return;
      }
      let active = true;
      setDataState(current => ({
        ...current, userId, loading: true, error: '',
      }));
      Promise.all([
        api.getProfile(userId),
        api.listPipelines(),
        api.listRuns(),
        api.listReminders(),
        api.listIntegrations(),
        api.listNotificationPreferences(),
        api.listNotificationDeliveries(),
        api.listLogs(),
        api.listAuditLogs(),
        api.getMetrics(),
      ]).then(([profile, pipelines, runs, reminders, integrations, notificationPreferences, notificationDeliveries, logs, auditLogs, metrics]) => {
        if (active) setDataState({
          userId, profile, pipelines, runs, reminders, integrations,
          notificationPreferences, notificationDeliveries, logs, auditLogs,
          metrics, loading: false, error: '',
        });
      }, error => {
        if (active) setDataState(current => ({
          ...current, userId, loading: false, error: error.message,
        }));
      });
      return () => { active = false; };
    }, [api, userId, dataRevision]);

    async function authenticate({ creating, email, password, name }) {
      const credentials = creating
        ? await api.signUp(email.trim(), password, name.trim(), new URL(location.pathname, location.origin).href)
        : await api.signIn(email.trim(), password);
      if (!credentials.session) return 'Check your email to confirm your account, then sign in.';
      setConnection(current => ({ ...current, user: credentials.user }));
      return '';
    }

    async function saveProject(project) {
      const saved = await api.saveProject(user, project);
      setProjectState(current => {
        if (current.userId !== userId) return current;
        const items = project.id
          ? current.items.map(item => item.id === saved.id ? saved : item)
          : [...current.items, saved];
        return { ...current, items };
      });
      return saved;
    }

    async function createOrganization(name) {
      await api.createOrganization(name);
      setDataRevision(revision => revision + 1);
    }

    async function createPipeline(pipeline) {
      if (!dataState.profile?.organization_id) throw new Error('Create an organization before adding a pipeline.');
      await api.createPipeline(dataState.profile.organization_id, pipeline);
      setDataRevision(revision => revision + 1);
    }

    async function createReminder(reminder) {
      if (!dataState.profile?.organization_id) throw new Error('Create an organization before adding a reminder.');
      await api.createReminder(dataState.profile.organization_id, reminder);
      setDataRevision(revision => revision + 1);
    }

    async function reviewReminder(id, reviewed) {
      await api.reviewReminder(id, reviewed);
      setDataRevision(revision => revision + 1);
    }

    async function runPipeline(id) {
      const result = await api.runPipeline(id);
      setDataRevision(revision => revision + 1);
      return result;
    }

    async function retryPipeline(id, runId) {
      const result = await api.retryPipeline(id, runId);
      setDataRevision(revision => revision + 1);
      return result;
    }

    async function cancelPipeline(id, runId) {
      const result = await api.cancelPipeline(id, runId);
      setDataRevision(revision => revision + 1);
      return result;
    }

    async function rollbackPipeline(id, request) {
      const result = await api.rollbackPipeline(id, request);
      setDataRevision(revision => revision + 1);
      return result;
    }

    async function syncPipeline(id) {
      const result = await api.syncPipeline(id);
      setDataRevision(revision => revision + 1);
      return result;
    }

    async function sendNotification(notification) {
      const result = await api.sendNotification(notification);
      setDataRevision(revision => revision + 1);
      return result;
    }

    const hasProjectState = Boolean(userId) && projectState.userId === userId;
    const hasDataState = Boolean(userId) && dataState.userId === userId;
    return {
      ...connection,
      apiUrlConfigured: Boolean(api?.apiUrlConfigured),
      projects: hasProjectState ? projectState.items : [],
      projectsLoading: !hasProjectState || projectState.loading,
      projectError: hasProjectState ? projectState.error : '',
      profile: hasDataState ? dataState.profile : null,
      pipelines: hasDataState ? dataState.pipelines : [],
      runs: hasDataState ? dataState.runs : [],
      reminders: hasDataState ? dataState.reminders : [],
      integrations: hasDataState ? dataState.integrations : [],
      notificationPreferences: hasDataState ? dataState.notificationPreferences : [],
      notificationDeliveries: hasDataState ? dataState.notificationDeliveries : [],
      logs: hasDataState ? dataState.logs : [],
      auditLogs: hasDataState ? dataState.auditLogs : [],
      metrics: hasDataState ? dataState.metrics : null,
      dataLoading: !hasDataState || dataState.loading,
      dataError: hasDataState ? dataState.error : '',
      authenticate,
      async signOut() {
        await api.signOut();
        setConnection(current => ({ ...current, user: null }));
      },
      saveProject,
      createOrganization,
      createPipeline,
      createReminder,
      reviewReminder,
      runPipeline,
      retryPipeline,
      cancelPipeline,
      rollbackPipeline,
      syncPipeline,
      sendNotification,
    };
  }

  function AuthPanel({ workspace }) {
    const [creating, setCreating] = useState(false);
    const [name, setName] = useState('');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [notice, setNotice] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    async function submit(event) {
      event.preventDefault();
      if (busy || !workspace.api) return;
      setBusy(true);
      setError('');
      setNotice('');
      try {
        const message = await workspace.authenticate({ creating, email, password, name });
        if (message) {
          setPassword('');
          setCreating(false);
          setNotice(message);
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    }

    return h('div', { className: 'paper', style: { padding: 28 } },
      h('h2', null, creating ? 'Create account' : 'Sign in'),
      workspace.demoMode && h('p', { role: 'status' },
        'Demo mode — Supabase is not configured. Sign in with any non-empty email and password to explore the interface.'),
      workspace.loading && h('p', { role: 'status' }, 'Checking your Supabase session…'),
      (workspace.error || error) && h('p', { role: 'alert' }, workspace.error || error),
      notice && h('p', { role: 'status' }, notice),
      h('form', { onSubmit: submit, style: { display: 'grid', gap: 12 } },
        creating && h('label', null, 'Full name', h('input', {
          value: name, onChange: event => setName(event.target.value), required: true, autoComplete: 'name',
        })),
        h('label', null, 'Email', h('input', {
          type: 'email', value: email, onChange: event => setEmail(event.target.value), required: true, autoComplete: 'email',
        })),
        h('label', null, 'Password', h('input', {
          type: 'password', value: password, onChange: event => setPassword(event.target.value), required: true,
          autoComplete: creating ? 'new-password' : 'current-password',
        })),
        h('button', { className: 'btn', disabled: busy || !workspace.api, type: 'submit' },
          busy ? 'Please wait…' : creating ? 'Create account' : 'Sign in'),
        h('button', { className: 'btn2', type: 'button', disabled: busy, onClick: () => {
          setCreating(!creating);
          setError('');
          setNotice('');
          setPassword('');
        } }, creating ? 'Back to sign in' : 'Create account'),
      ),
    );
  }

  function ProjectsPanel({ workspace }) {
    const [draft, setDraft] = useState(null);
    const [pinnedOnly, setPinnedOnly] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const { projects, projectsLoading, projectError } = workspace;
    const shown = pinnedOnly ? projects.filter(project => project.pinned) : projects;

    async function submit(event) {
      event.preventDefault();
      if (busy || !draft) return;
      setBusy(true);
      setError('');
      setNotice('');
      try {
        await workspace.saveProject(draft);
        setDraft(null);
        setPinnedOnly(false);
        setNotice('Project saved to Supabase.');
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    }

    function edit(project) {
      setDraft(project);
      setError('');
      setNotice('');
    }

    function change(field, value) {
      setDraft(current => ({ ...current, [field]: value }));
    }

    return h('section', null,
      h('h2', null, 'Main Projects'),
      h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 } },
        h('button', { className: 'btn2', 'aria-pressed': !pinnedOnly, onClick: () => setPinnedOnly(false) }, 'All projects'),
        h('button', { className: 'btn2', 'aria-pressed': pinnedOnly, onClick: () => setPinnedOnly(true) }, 'Important (pinned)'),
        h('button', { className: 'btn', disabled: busy || projectsLoading || Boolean(projectError), onClick: () => edit({ ...emptyProject }) }, 'Add project'),
      ),
      projectsLoading && h('p', { role: 'status' }, 'Loading projects…'),
      (projectError || error) && h('p', { role: 'alert' }, projectError || error),
      notice && h('p', { role: 'status' }, notice),
      draft && h('form', { className: 'card', onSubmit: submit, style: { display: 'grid', gap: 10, marginBottom: 14 } },
        h('h3', null, draft.id ? 'Edit project' : 'New project'),
        h('label', null, 'Project name', h('input', { value: draft.name, required: true, onChange: event => change('name', event.target.value) })),
        h('label', null, 'Repository', h('input', { value: draft.repo, placeholder: 'owner/repository', onChange: event => change('repo', event.target.value) })),
        h('label', null, 'Description', h('textarea', { value: draft.desc, onChange: event => change('desc', event.target.value) })),
        h('label', null, 'Status', h('select', { value: draft.status, onChange: event => change('status', event.target.value) },
          ['Active', 'Paused', 'Rollback Required', 'Archived'].map(status => h('option', { key: status }, status)))),
        h('label', null, 'Priority', h('select', { value: draft.priority, onChange: event => change('priority', event.target.value) },
          ['low', 'medium', 'high', 'critical'].map(priority => h('option', { key: priority }, priority)))),
        h('label', null, h('input', { type: 'checkbox', checked: draft.pinned, onChange: event => change('pinned', event.target.checked) }), ' Pin project'),
        h('div', { style: { display: 'flex', gap: 8 } },
          h('button', { className: 'btn', type: 'submit', disabled: busy }, busy ? 'Saving…' : 'Save project'),
          h('button', { className: 'btn2', type: 'button', disabled: busy, onClick: () => edit(null) }, 'Cancel'),
        ),
      ),
      !projectsLoading && !projectError && !shown.length && h('p', null, pinnedOnly ? 'No pinned projects.' : 'No projects yet. Add your first project.'),
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12 } },
        shown.map(project => h('article', { className: 'card', key: project.id },
          h('h3', null, project.name),
          h('p', { className: 'small' }, [project.status, project.priority, project.pinned ? 'Pinned' : ''].filter(Boolean).join(' · ')),
          project.repo && h('p', { className: 'small mono' }, project.repo),
          project.desc && h('p', { className: 'small' }, project.desc),
          h('button', { className: 'btn2', disabled: busy, onClick: () => edit(project) }, 'Edit'),
        )),
      ),
    );
  }

  function ProjectPanel({ workspace }) {
    const [page, setPage] = useState('Projects');
    const [organizationName, setOrganizationName] = useState('');
    const [pipeline, setPipeline] = useState({
      name: '', repo: '', branch: 'main', workflowId: '', provider: 'GitHub Actions', hasWorkflowDispatch: false,
    });
    const [reminder, setReminder] = useState({ remindAt: '', channel: 'in_app' });
    const [rollbackTarget, setRollbackTarget] = useState('');
    const [rollbackEnvironment, setRollbackEnvironment] = useState('production');
    const [notification, setNotification] = useState({
      event: 'manual_notification', severity: 'medium', project: '', message: '',
    });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const pages = ['Projects', 'Pipelines', 'Runs', 'Logs', 'Reminders', 'Integrations', 'Notifications', 'Audit', 'Metrics'];

    async function perform(action) {
      if (busy) return;
      setBusy(true);
      setError('');
      setNotice('');
      try {
        const result = await action();
        if (result?.accepted && result.provider === 'github') {
          setNotice(result.action === 'cancel'
            ? 'GitHub accepted the cancellation. Run status will update when the webhook arrives.'
            : result.action === 'rollback'
              ? 'GitHub accepted the rollback dispatch. Run status will update when the webhook arrives.'
              : 'GitHub accepted the request. The run will appear after the webhook arrives.');
        } else if (result?.accepted && result.provider === 'Resend') {
          setNotice('Email accepted by Resend and recorded in delivery history.');
        } else if (result?.accepted) {
          setNotice(result.action === 'cancel'
            ? `${result.provider} accepted the cancellation. Refresh provider status to confirm it.`
            : `${result.provider} accepted the pipeline run. Refresh provider status to sync its result.`);
        } else if (result?.synced) {
          setNotice(`Synced ${result.synced} provider run${result.synced === 1 ? '' : 's'}.`);
        }
        return result;
      } catch (failure) {
        setError(failure.message);
      } finally {
        setBusy(false);
      }
    }

    const orgId = workspace.profile?.organization_id;
    const pipelineNames = Object.fromEntries(workspace.pipelines.map(item => [item.id, item.name]));
    let content;

    if (page === 'Projects') {
      content = h(ProjectsPanel, { workspace });
    } else if (page === 'Pipelines') {
      content = h('section', null,
        h('h2', null, 'Pipelines'),
        orgId ? h('form', {
          className: 'card',
          onSubmit: event => {
            event.preventDefault();
            void perform(async () => {
              await workspace.createPipeline(pipeline);
              setPipeline({ name: '', repo: '', branch: 'main', workflowId: '', provider: 'GitHub Actions', hasWorkflowDispatch: false });
            });
          },
          style: { display: 'grid', gap: 10, marginBottom: 14 },
        },
        h('h3', null, 'Add pipeline'),
        h('label', null, 'Pipeline name', h('input', { value: pipeline.name, required: true, onChange: event => setPipeline({ ...pipeline, name: event.target.value }) })),
        h('label', null, 'Repository (owner/repository)', h('input', { value: pipeline.repo, required: true, placeholder: 'owner/repository', onChange: event => setPipeline({ ...pipeline, repo: event.target.value }) })),
        h('label', null, 'Branch', h('input', { value: pipeline.branch, required: true, onChange: event => setPipeline({ ...pipeline, branch: event.target.value }) })),
        h('label', null, ({
          'GitHub Actions': 'Workflow file or numeric ID',
          Jenkins: 'Jenkins job path',
          'Azure DevOps': 'Azure pipeline ID',
          'Bitbucket Pipelines': 'Custom pipeline name (optional)',
          'GitLab CI/CD': 'Pipeline identifier (optional)',
          CircleCI: 'Pipeline identifier (optional)',
        })[pipeline.provider] || 'Pipeline identifier', h('input', {
          value: pipeline.workflowId,
          required: ['GitHub Actions', 'Jenkins', 'Azure DevOps'].includes(pipeline.provider),
          placeholder: pipeline.provider === 'GitHub Actions' ? 'deploy.yml' : pipeline.provider === 'Jenkins' ? 'folder/job' : pipeline.provider === 'Azure DevOps' ? '42' : 'Optional',
          onChange: event => setPipeline({ ...pipeline, workflowId: event.target.value }),
        })),
        h('label', null, 'Provider', h('select', { value: pipeline.provider, onChange: event => setPipeline({ ...pipeline, provider: event.target.value }) },
          ['GitHub Actions', 'Jenkins', 'CircleCI', 'GitLab CI/CD', 'Azure DevOps', 'Bitbucket Pipelines'].map(provider => h('option', { key: provider, value: provider }, provider)))),
        pipeline.provider === 'GitHub Actions' && h('label', null,
          h('input', { type: 'checkbox', checked: pipeline.hasWorkflowDispatch, onChange: event => setPipeline({ ...pipeline, hasWorkflowDispatch: event.target.checked }) }),
          ' Workflow supports workflow_dispatch'),
        h('button', { className: 'btn', type: 'submit', disabled: busy }, busy ? 'Saving…' : 'Save pipeline')) :
        h('p', { className: 'card' }, 'Create an organization before adding pipelines.'),
        workspace.pipelines.length ? h('div', { style: { display: 'grid', gap: 10, marginTop: 14 } },
          workspace.pipelines.map(item => h('article', { className: 'card', key: item.id },
            h('h3', null, item.name),
            h('p', { className: 'small' }, `${item.provider} · ${item.repo || 'No repository'} · ${item.branch || 'No branch'} · ${item.workflow_id || 'No workflow'}`),
            item.provider === 'GitHub Actions' && !item.has_workflow_dispatch &&
              h('p', { role: 'status' }, 'Add workflow_dispatch to the GitHub workflow before enabling runs.'),
            item.provider !== 'GitHub Actions' && h('button', {
              className: 'btn', type: 'button', disabled: busy || !workspace.apiUrlConfigured,
              onClick: () => void perform(() => workspace.runPipeline(item.id)),
            }, 'Run pipeline'),
            item.provider === 'GitHub Actions' && item.has_workflow_dispatch && h('button', {
              className: 'btn', type: 'button', disabled: busy || !workspace.apiUrlConfigured,
              onClick: () => void perform(() => workspace.runPipeline(item.id)),
            }, 'Run workflow'),
            item.provider === 'GitHub Actions' && item.has_workflow_dispatch && h('form', {
              style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 },
              onSubmit: event => {
                event.preventDefault();
                void perform(() => workspace.rollbackPipeline(item.id, {
                  target_commit_sha: rollbackTarget,
                  target_workflow: item.workflow_id,
                  environment: rollbackEnvironment,
                  confirm: true,
                }));
              },
            },
            h('input', { value: rollbackTarget, required: true, minLength: 7, maxLength: 64, placeholder: 'Target commit SHA', onChange: event => setRollbackTarget(event.target.value) }),
            h('input', { value: rollbackEnvironment, required: true, maxLength: 100, 'aria-label': 'Rollback environment', onChange: event => setRollbackEnvironment(event.target.value) }),
            h('label', null, h('input', { type: 'checkbox', required: true }), ' Confirm rollback'),
            h('button', { className: 'btn2', type: 'submit', disabled: busy || !workspace.apiUrlConfigured }, 'Rollback')),
          ))) : !workspace.dataLoading && h('p', { className: 'card' }, 'No pipelines saved yet.'),
      );
    } else if (page === 'Runs') {
      content = h('section', null,
        h('h2', null, 'Pipeline runs'),
        workspace.apiUrlConfigured && workspace.pipelines.some(item => item.provider !== 'GitHub Actions') && h('button', {
          className: 'btn2', type: 'button', disabled: busy,
          onClick: () => void perform(async () => {
            const results = await Promise.all(workspace.pipelines
              .filter(item => item.provider !== 'GitHub Actions')
              .map(item => workspace.syncPipeline(item.id)));
            return { synced: results.reduce((count, result) => count + (result.synced || 0), 0) };
          }),
        }, 'Refresh provider statuses'),
        workspace.runs.length ? h('div', { style: { display: 'grid', gap: 10 } },
          workspace.runs.map(run => h('article', { className: 'card', key: run.id },
            h('h3', null, pipelineNames[run.pipeline_id] || 'Pipeline'),
            h('p', { className: 'small' }, [
              run.status, run.branch, run.commit_sha?.slice(0, 8),
              run.started_at ? new Date(run.started_at).toLocaleString() : '',
              run.duration_sec == null ? '' : `${run.duration_sec}s`,
            ].filter(Boolean).join(' · ')),
            run.status === 'failed' || run.status === 'cancelled'
              ? h('button', { className: 'btn2', disabled: busy || !workspace.apiUrlConfigured, onClick: () => void perform(() => workspace.retryPipeline(run.pipeline_id, run.id)) }, 'Retry as new run')
              : run.status === 'running'
                ? h('button', { className: 'btn2', disabled: busy || !workspace.apiUrlConfigured, onClick: () => void perform(() => workspace.cancelPipeline(run.pipeline_id, run.id)) }, 'Cancel run')
                : null,
          ))) : !workspace.dataLoading && h('p', { className: 'card' }, 'No runs are available for this account.'),
      );
    } else if (page === 'Logs') {
      content = h('section', null,
        h('h2', null, 'Redacted pipeline logs'),
        workspace.logs.length ? h('div', { style: { display: 'grid', gap: 10 } },
          workspace.logs.map(item => h('article', { className: 'card', key: item.id },
            h('p', { className: 'small' }, `${item.level || 'LOG'} · ${new Date(item.created_at).toLocaleString()} · run ${item.run_id}`),
            h('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, item.message),
          ))) : !workspace.dataLoading && h('p', { className: 'card' }, 'No logs available.'),
      );
    } else if (page === 'Reminders') {
      content = h('section', null,
        h('h2', null, 'Reminders'),
        orgId ? h('form', {
          className: 'card',
          onSubmit: event => {
            event.preventDefault();
            void perform(async () => {
              await workspace.createReminder(reminder);
              setReminder({ remindAt: '', channel: 'in_app' });
            });
          },
          style: { display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 14 },
        },
        h('label', null, 'Reminder time', h('input', { type: 'datetime-local', value: reminder.remindAt, required: true, onChange: event => setReminder({ ...reminder, remindAt: event.target.value }) })),
        h('button', { className: 'btn', type: 'submit', disabled: busy }, 'Add reminder')) :
        h('p', { className: 'card' }, 'Create an organization before adding shared reminders.'),
        workspace.reminders.length ? h('div', { style: { display: 'grid', gap: 10 } },
          workspace.reminders.map(item => h('article', { className: 'card', key: item.id },
            h('strong', null, item.project_id ? workspace.projects.find(project => project.id === item.project_id)?.name || 'Project reminder' : 'Workspace reminder'),
            h('p', { className: 'small' }, `${new Date(item.remind_at).toLocaleString()} · ${item.is_reviewed ? 'Reviewed' : 'Pending'}`),
            !item.is_reviewed && h('button', { className: 'btn2', disabled: busy, onClick: () => void perform(() => workspace.reviewReminder(item.id, true)) }, 'Mark reviewed'),
          ))) : !workspace.dataLoading && h('p', { className: 'card' }, 'No reminders saved yet.'),
      );
    } else if (page === 'Integrations') {
      content = h('section', null,
        h('h2', null, 'Integrations'),
        h('p', { className: 'small' }, 'Provider credentials are configured only in the backend environment; the browser never receives provider secrets.'),
        workspace.integrations.length ? workspace.integrations.map(item => h('article', { className: 'card', key: item.id, style: { marginBottom: 10 } },
          h('strong', null, item.provider),
          h('p', { className: 'small' }, `${item.status} · ${item.auth_method}${item.last_sync_at ? ` · Last sync ${new Date(item.last_sync_at).toLocaleString()}` : ''}`),
          item.last_error && h('p', { role: 'alert' }, item.last_error),
        )) : !workspace.dataLoading && h('p', { className: 'card' }, 'No provider integrations are connected.'),
      );
    } else if (page === 'Notifications') {
      content = h('section', null,
        h('h2', null, 'Notification preferences'),
        h('form', {
          className: 'card',
          onSubmit: event => {
            event.preventDefault();
            void perform(async () => {
              const result = await workspace.sendNotification(notification);
              setNotification({ event: 'manual_notification', severity: 'medium', project: '', message: '' });
              return result;
            });
          },
          style: { display: 'grid', gap: 10, marginBottom: 14 },
        },
        h('p', { className: 'small' }, 'Send a real email to your signed-in address. Configure Resend on the backend; delivery credentials are never stored in the browser.'),
        h('label', null, 'Event', h('input', { value: notification.event, required: true, maxLength: 200, onChange: event => setNotification({ ...notification, event: event.target.value }) })),
        h('label', null, 'Severity', h('select', { value: notification.severity, onChange: event => setNotification({ ...notification, severity: event.target.value }) },
          ['critical', 'high', 'medium', 'low'].map(severity => h('option', { key: severity, value: severity }, severity)))),
        h('label', null, 'Project', h('input', { value: notification.project, required: true, maxLength: 200, onChange: event => setNotification({ ...notification, project: event.target.value }) })),
        h('label', null, 'Message', h('textarea', { value: notification.message, required: true, maxLength: 5000, onChange: event => setNotification({ ...notification, message: event.target.value }) })),
        h('button', { className: 'btn', type: 'submit', disabled: busy || !workspace.apiUrlConfigured }, busy ? 'Sending…' : 'Send email to me')),
        workspace.notificationPreferences.length ? workspace.notificationPreferences.map(item => h('article', { className: 'card', key: item.id, style: { marginBottom: 10 } },
          h('strong', null, item.event),
          h('p', { className: 'small' }, `${item.channel} · ${item.severity} · ${item.timezone}`),
          item.quiet_hours_start && h('p', { className: 'small' }, `Quiet hours ${item.quiet_hours_start}–${item.quiet_hours_end}`),
        )) : !workspace.dataLoading && h('p', { className: 'card' }, 'No notification preferences configured.'),
        h('h3', null, 'Recent delivery records'),
        workspace.notificationDeliveries.length ? workspace.notificationDeliveries.map(item => h('article', { className: 'card', key: item.id, style: { marginBottom: 10 } },
          h('strong', null, item.event || 'Notification'),
          h('p', { className: 'small' }, `${item.status || 'unknown'} · ${item.channel || 'no channel'} · ${new Date(item.created_at).toLocaleString()}`),
        )) : !workspace.dataLoading && h('p', { className: 'card' }, 'No notification deliveries recorded.'),
      );
    } else if (page === 'Audit') {
      content = h('section', null,
        h('h2', null, 'Audit log'),
        workspace.auditLogs.length ? h('div', { style: { display: 'grid', gap: 10 } },
          workspace.auditLogs.map(item => h('article', { className: 'card', key: item.id },
            h('strong', null, item.action),
            h('p', { className: 'small' }, `${item.entity || 'Workspace'} · ${new Date(item.created_at).toLocaleString()}`),
            item.details && h('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, JSON.stringify(item.details, null, 2)),
          ))) : !workspace.dataLoading && h('p', { className: 'card' }, 'No audit records available, or your role does not have audit access.'),
      );
    } else {
      const metrics = workspace.metrics;
      content = h('section', null,
        h('h2', null, 'Workspace metrics'),
        metrics && h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 } },
          [['Success rate', `${metrics.successRate}%`], ['Successful', metrics.success], ['Failed', metrics.failed], ['Running', metrics.running], ['Cancelled', metrics.cancelled]]
            .map(([label, value]) => h('article', { className: 'card', key: label }, h('p', { className: 'small' }, label), h('h3', null, value))),
        ),
      );
    }

    return h('section', null,
      h('h2', null, 'Live Supabase workspace'),
      workspace.dataLoading && h('p', { role: 'status' }, 'Loading your workspace data…'),
      workspace.dataError && h('p', { role: 'alert' }, workspace.dataError),
      error && h('p', { role: 'alert' }, error),
      notice && h('p', { role: 'status' }, notice),
      !orgId && !workspace.dataLoading && h('form', {
        className: 'card',
        onSubmit: event => {
          event.preventDefault();
          void perform(async () => {
            await workspace.createOrganization(organizationName);
            setOrganizationName('');
          });
        },
        style: { display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 14 },
      },
      h('label', null, 'Set up an organization to enable pipelines and reminders', h('input', {
        value: organizationName, required: true, maxLength: 120, onChange: event => setOrganizationName(event.target.value),
      })),
      h('button', { className: 'btn', type: 'submit', disabled: busy }, 'Create organization')),
      h('nav', { 'aria-label': 'Live workspace sections', style: { display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 } },
        pages.map(item => h('button', {
          className: page === item ? 'btn' : 'btn2',
          type: 'button',
          'aria-pressed': page === item,
          key: item,
          onClick: () => { setPage(item); setError(''); },
        }, item)),
      ),
      content,
    );
  }

  return { useWorkspace, AuthPanel, ProjectPanel };
}
