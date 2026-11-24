const projectFields = 'id,owner_id,organization_id,name,repository,description,status,priority,is_pinned,sort_order,last_reviewed_at,next_review_at';
const statuses = { active: 'Active', paused: 'Paused', rollback_required: 'Rollback Required', archived: 'Archived' };

export const emptyProject = { name: '', repo: '', desc: '', status: 'Active', priority: 'medium', pinned: false };

const pipelineFields = 'id,organization_id,project_id,integration_id,name,provider,repo,branch,workflow_id,has_workflow_dispatch,is_selected,priority,sort_order,created_at';

export async function connectWorkspace({
  loadConfig = () => fetch(new URL('../supabase.local.json', import.meta.url), { cache: 'no-store' }),
  loadSDK = () => import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.98.0/+esm'),
} = {}) {
  const response = await loadConfig();
  if (!response.ok) throw new Error('Create supabase.local.json from the included example and serve this folder over HTTP.');
  const { url, publicKey, apiUrl } = await response.json();
  let endpoint;
  try {
    endpoint = new URL(url);
  } catch {
    throw new Error('Enter a valid Supabase API URL in supabase.local.json.');
  }
  if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    throw new Error('Enter a Supabase HTTP or HTTPS API URL without credentials or query parameters.');
  }
  if (!isPublicKey(publicKey)) throw new Error('Use a Supabase publishable key or legacy anon key in supabase.local.json.');
  const backendUrl = apiUrl ? validateApiUrl(apiUrl) : '';
  const { createClient } = await loadSDK();
  return createWorkspace(createClient(endpoint.href, publicKey), backendUrl);
}

function validateApiUrl(value) {
  let endpoint;
  try {
    endpoint = new URL(value);
  } catch {
    throw new Error('Enter a valid backend API URL in supabase.local.json.');
  }
  if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) {
    throw new Error('Enter an HTTP or HTTPS backend API URL without credentials or query parameters.');
  }
  return endpoint.href.replace(/\/+$/, '');
}

function isPublicKey(key) {
  if (typeof key !== 'string') return false;
  if (key.startsWith('sb_publishable_') && key.length > 15) return true;
  try {
    const parts = key.split('.');
    if (parts.length !== 3) return false;
    return JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/'))).role === 'anon';
  } catch {
    return false;
  }
}

async function result(request) {
  const { data, error } = await request;
  if (error) throw new Error(error.message);
  return data;
}

function toProject(row) {
  const status = statuses[row.status];
  if (!status) throw new Error(`Unknown project status returned by Supabase: ${row.status}`);
  return {
    id: row.id, ownerId: row.owner_id, organizationId: row.organization_id,
    name: row.name, repo: row.repository ?? '', desc: row.description ?? '',
    status, priority: row.priority, pinned: row.is_pinned, order: row.sort_order,
    lastReviewed: row.last_reviewed_at?.slice(0, 10) ?? '—',
    nextReview: row.next_review_at?.slice(0, 10) ?? '—',
  };
}

export function createWorkspace(client, backendUrl = '') {
  const auth = client.auth;
  async function backendRequest(path, method = 'POST', body) {
    if (!backendUrl) throw new Error('Set apiUrl in supabase.local.json to enable server-side provider actions.');
    const { data: { session }, error: sessionError } = await auth.getSession();
    if (sessionError) throw new Error(sessionError.message);
    if (!session?.access_token) throw new Error('Sign in before using provider actions.');
    const response = await fetch(`${backendUrl}/api/v1/${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        throw new Error(`Backend returned invalid JSON (HTTP ${response.status}).`);
      }
    }
    if (!response.ok) throw new Error(payload.detail || `Backend request failed (HTTP ${response.status}).`);
    return payload;
  }

  return {
    apiUrlConfigured: Boolean(backendUrl),
    async currentUser() {
      const { session } = await result(auth.getSession());
      return session ? (await result(auth.getUser())).user : null;
    },
    onAuthChange(callback) {
      const { data: { subscription } } = auth.onAuthStateChange(callback);
      return () => subscription.unsubscribe();
    },
    async signIn(email, password) {
      return result(auth.signInWithPassword({ email, password }));
    },
    async signUp(email, password, name, redirectTo) {
      return result(auth.signUp({ email, password, options: { data: { full_name: name }, emailRedirectTo: redirectTo } }));
    },
    async signOut() {
      await result(auth.signOut({ scope: 'local' }));
    },
    async getProfile(userId) {
      return result(client.from('profiles').select('id,organization_id,role').eq('id', userId).maybeSingle());
    },
    async createOrganization(name) {
      const cleanName = name.trim();
      if (!cleanName) throw new Error('Enter an organization name.');
      return result(client.rpc('create_workspace_organization', { p_name: cleanName }));
    },
    async listProjects() {
      const rows = await result(client.from('projects').select(projectFields).order('sort_order').order('created_at').order('id'));
      return rows.map(toProject);
    },
    async listPipelines() {
      return result(client.from('pipelines').select(pipelineFields).order('sort_order').order('created_at'));
    },
    async createPipeline(organizationId, pipeline) {
      const name = pipeline.name.trim();
      const repo = pipeline.repo.trim();
      const workflowId = pipeline.workflowId.trim();
      if (!name) throw new Error('Enter a pipeline name.');
      if (!['GitHub Actions', 'Jenkins', 'CircleCI', 'GitLab CI/CD', 'Azure DevOps', 'Bitbucket Pipelines'].includes(pipeline.provider)) {
        throw new Error('Choose a supported pipeline provider.');
      }
      const repositoryIsValid = pipeline.provider === 'GitLab CI/CD'
        ? /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)+$/.test(repo)
        : /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo);
      if (!repositoryIsValid) {
        throw new Error('Repository must be in owner/repository format.');
      }
      const branch = pipeline.branch.trim() || 'main';
      if (!/^[A-Za-z0-9_./-]{1,255}$/.test(branch) || branch.includes('..') || branch.startsWith('/') || branch.endsWith('/')) {
        throw new Error('Enter a valid branch name.');
      }
      if (pipeline.provider === 'GitHub Actions' && !/^(?:\d+|[A-Za-z0-9_.-]+\.ya?ml)$/i.test(workflowId)) {
        throw new Error('Enter a workflow filename (for example deploy.yml) or numeric GitHub workflow ID.');
      }
      if (pipeline.provider === 'Jenkins' && !/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(workflowId)) {
        throw new Error('Enter a safe Jenkins job path, such as folder/job.');
      }
      if (pipeline.provider === 'Azure DevOps' && !/^\d+$/.test(workflowId)) {
        throw new Error('Enter a numeric Azure DevOps pipeline ID.');
      }
      if (pipeline.provider === 'Bitbucket Pipelines' && workflowId && !/^[A-Za-z0-9_.-]+$/.test(workflowId)) {
        throw new Error('Enter a valid Bitbucket custom pipeline name.');
      }
      const row = await result(client.from('pipelines').insert({
        organization_id: organizationId,
        project_id: pipeline.projectId || null,
        name,
        provider: pipeline.provider,
        repo,
        branch,
        workflow_id: workflowId || null,
        has_workflow_dispatch: pipeline.hasWorkflowDispatch,
      }).select(pipelineFields).single());
      return row;
    },
    async listRuns() {
      return result(client.from('pipeline_runs')
        .select('id,pipeline_id,run_number,status,branch,commit_sha,triggered_by,trigger_type,environment,started_at,ended_at,duration_sec,is_retry,parent_run_id,created_at')
        .order('started_at', { ascending: false }).limit(100));
    },
    async listReminders() {
      return result(client.from('reminders')
        .select('id,organization_id,project_id,remind_at,channel,is_reviewed,reviewed_by,reviewed_at')
        .order('remind_at'));
    },
    async createReminder(organizationId, reminder) {
      if (!reminder.remindAt || Number.isNaN(Date.parse(reminder.remindAt))) {
        throw new Error('Choose a valid reminder date and time.');
      }
      return result(client.from('reminders').insert({
        organization_id: organizationId,
        project_id: reminder.projectId || null,
        remind_at: new Date(reminder.remindAt).toISOString(),
        channel: reminder.channel,
      }).select('id,organization_id,project_id,remind_at,channel,is_reviewed,reviewed_by,reviewed_at').single());
    },
    async reviewReminder(id, reviewed) {
      return result(client.from('reminders').update({
        is_reviewed: reviewed,
        reviewed_by: reviewed ? (await result(auth.getUser())).user.id : null,
        reviewed_at: reviewed ? new Date().toISOString() : null,
      }).eq('id', id).select('id,organization_id,project_id,remind_at,channel,is_reviewed,reviewed_by,reviewed_at').single());
    },
    async listIntegrations() {
      return result(client.from('integrations')
        .select('id,organization_id,provider,auth_method,status,last_sync_at,last_error,sync_method,created_at')
        .order('created_at', { ascending: false }));
    },
    async listNotificationPreferences() {
      return result(client.from('notification_preferences')
        .select('id,organization_id,event,channel,severity,quiet_hours_start,quiet_hours_end,critical_bypass_quiet_hours,timezone'));
    },
    async listNotificationDeliveries() {
      return result(client.from('notification_deliveries')
        .select('id,organization_id,event,channel,status,provider,dedupe_key,created_at')
        .order('created_at', { ascending: false }).limit(100));
    },
    async listLogs() {
      return result(client.from('pipeline_logs')
        .select('id,run_id,stage_id,level,message,redaction_count,copied_to_storage,created_at')
        .order('created_at', { ascending: false }).limit(100));
    },
    async listAuditLogs() {
      return result(client.from('audit_logs')
        .select('id,organization_id,actor_id,action,entity,details,created_at')
        .order('created_at', { ascending: false }).limit(100));
    },
    async getMetrics() {
      const counts = await Promise.all(['success', 'failed', 'running', 'cancelled'].map(async status => {
        const { count, error } = await client.from('pipeline_runs')
          .select('id', { count: 'exact', head: true }).eq('status', status);
        if (error) throw new Error(error.message);
        return [status, count ?? 0];
      }));
      const values = Object.fromEntries(counts);
      const denominator = values.success + values.failed;
      return {
        ...values,
        successRate: denominator ? Math.round(values.success / denominator * 1000) / 10 : 0,
      };
    },
    runPipeline(id) {
      return backendRequest(`pipelines/${encodeURIComponent(id)}/run`);
    },
    retryPipeline(id, runId) {
      return backendRequest(`pipelines/${encodeURIComponent(id)}/retry?run_id=${encodeURIComponent(runId)}`);
    },
    cancelPipeline(id, runId) {
      return backendRequest(`pipelines/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}/cancel`);
    },
    syncPipeline(id) {
      return backendRequest(`pipelines/${encodeURIComponent(id)}/sync`);
    },
    rollbackPipeline(id, request) {
      return backendRequest(`pipelines/${encodeURIComponent(id)}/rollback`, 'POST', request);
    },
    async sendNotification(notification) {
      const response = await backendRequest('notifications/send', 'POST', notification);
      return response;
    },
    async saveProject(user, project) {
      const name = project.name.trim();
      if (!name) throw new Error('Enter a project name.');
      const status = Object.keys(statuses).find(key => statuses[key] === project.status);
      if (!status) throw new Error('Choose a valid project status.');
      const changes = {
        name, repository: project.repo.trim() || null, description: project.desc.trim() || null,
        status, priority: project.priority, is_pinned: project.pinned,
      };
      const table = client.from('projects');
      const request = project.id ? table.update(changes).eq('id', project.id) :
        table.insert({ ...changes, owner_id: user.id });
      return toProject(await result(request.select(projectFields).single()));
    },
  };
}

export function createDemoWorkspace() {
  let user = null;
  let profile = null;
  let organization = null;
  let projects = [
    {
      id: 'demo-project-1', ownerId: 'demo-user', organizationId: 'demo-org',
      name: 'Checkout Platform', repo: 'example/checkout-platform', desc: 'Demo project',
      status: 'Active', priority: 'high', pinned: true, order: 0,
      lastReviewed: '2026-11-24', nextReview: '2026-12-01',
    },
  ];
  const pipelines = [
    {
      id: 'demo-pipeline-1', organization_id: 'demo-org', name: 'Production deploy',
      provider: 'GitHub Actions', repo: 'example/checkout-platform', branch: 'main',
      workflow_id: 'deploy.yml', has_workflow_dispatch: true,
    },
  ];
  const runs = [
    {
      id: 'demo-run-1', pipeline_id: 'demo-pipeline-1', run_number: 42,
      status: 'success', branch: 'main', commit_sha: 'a1b2c3d4e5f6',
      started_at: '2026-11-24T12:00:00Z', ended_at: '2026-11-24T12:05:00Z',
      duration_sec: 300,
    },
  ];
  const reminders = [];
  const integrations = [
    { id: 'demo-integration-1', provider: 'github', auth_method: 'GitHub App', status: 'connected' },
  ];
  const notificationPreferences = [];
  const notificationDeliveries = [];
  const logs = [
    {
      id: 'demo-log-1', run_id: 'demo-run-1', level: 'INFO',
      message: 'Demo log only: configure Supabase and providers for real data.',
      created_at: '2026-11-24T12:05:00Z',
    },
  ];
  const auditLogs = [];
  let authCallback = null;

  function emitSession() {
    authCallback?.('TOKEN_REFRESHED', user ? { user } : null);
  }

  return {
    apiUrlConfigured: false,
    demoMode: true,
    async currentUser() {
      return user;
    },
    onAuthChange(callback) {
      authCallback = callback;
      return () => { authCallback = null; };
    },
    async signIn(email, password) {
      if (!email || !password) throw new Error('Enter email and password.');
      user = { id: 'demo-user', email };
      profile = { id: 'demo-user', organization_id: organization?.id ?? 'demo-org', role: 'owner' };
      emitSession();
      return { user, session: { user } };
    },
    async signUp(email, password, name) {
      if (!email || !password || !name) throw new Error('Enter name, email, and password.');
      user = { id: 'demo-user', email, user_metadata: { full_name: name } };
      profile = { id: 'demo-user', organization_id: null, role: 'owner' };
      emitSession();
      return { user, session: { user } };
    },
    async signOut() {
      user = null;
      profile = null;
      emitSession();
    },
    async getProfile() {
      return profile;
    },
    async createOrganization(name) {
      const cleanName = name.trim();
      if (!cleanName) throw new Error('Enter an organization name.');
      organization = { id: 'demo-org', name: cleanName };
      profile = { id: 'demo-user', organization_id: organization.id, role: 'owner' };
      return organization.id;
    },
    async listProjects() {
      return projects;
    },
    async saveProject(_user, project) {
      const name = project.name.trim();
      if (!name) throw new Error('Enter a project name.');
      const status = Object.keys(statuses).find(key => statuses[key] === project.status);
      if (!status) throw new Error('Choose a valid project status.');
      if (project.id) {
        const index = projects.findIndex(item => item.id === project.id);
        projects[index] = {
          ...projects[index], name, repo: project.repo.trim(), desc: project.desc.trim(),
          status: statuses[status], priority: project.priority, pinned: project.pinned,
        };
        return projects[index];
      }
      const saved = {
        id: `demo-project-${projects.length + 1}`, ownerId: 'demo-user',
        organizationId: organization?.id ?? 'demo-org', name, repo: project.repo.trim(),
        desc: project.desc.trim(), status: statuses[status], priority: project.priority,
        pinned: project.pinned, order: projects.length,
        lastReviewed: '—', nextReview: '—',
      };
      projects = [...projects, saved];
      return saved;
    },
    async listPipelines() { return pipelines; },
    async createPipeline(_organizationId, pipeline) {
      const item = {
        id: `demo-pipeline-${pipelines.length + 1}`, organization_id: 'demo-org',
        name: pipeline.name.trim(), provider: pipeline.provider, repo: pipeline.repo.trim(),
        branch: pipeline.branch.trim() || 'main', workflow_id: pipeline.workflowId.trim() || null,
        has_workflow_dispatch: pipeline.hasWorkflowDispatch,
      };
      pipelines.push(item);
      return item;
    },
    async listRuns() { return runs; },
    async listReminders() { return reminders; },
    async createReminder(_organizationId, reminder) {
      const item = {
        id: `demo-reminder-${reminders.length + 1}`, organization_id: 'demo-org',
        project_id: null, remind_at: new Date(reminder.remindAt).toISOString(),
        channel: reminder.channel, is_reviewed: false,
      };
      reminders.push(item);
      return item;
    },
    async reviewReminder(id, reviewed) {
      const item = reminders.find(row => row.id === id);
      if (item) {
        item.is_reviewed = reviewed;
        item.reviewed_at = reviewed ? new Date().toISOString() : null;
      }
      return item;
    },
    async listIntegrations() { return integrations; },
    async listNotificationPreferences() { return notificationPreferences; },
    async listNotificationDeliveries() { return notificationDeliveries; },
    async listLogs() { return logs; },
    async listAuditLogs() { return auditLogs; },
    async getMetrics() {
      return { success: 1, failed: 0, running: 0, cancelled: 0, successRate: 100 };
    },
    async runPipeline() {
      throw new Error('Demo mode: provider actions require live backend credentials.');
    },
    async retryPipeline() {
      throw new Error('Demo mode: provider actions require live backend credentials.');
    },
    async cancelPipeline() {
      throw new Error('Demo mode: provider actions require live backend credentials.');
    },
    async rollbackPipeline() {
      throw new Error('Demo mode: provider actions require live backend credentials.');
    },
    async syncPipeline() {
      throw new Error('Demo mode: provider status synchronization requires live credentials.');
    },
    async sendNotification() {
      throw new Error('Demo mode: notification delivery requires live Resend credentials.');
    },
  };
}
