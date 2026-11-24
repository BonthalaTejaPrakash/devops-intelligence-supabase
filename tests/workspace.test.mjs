import assert from 'node:assert/strict';
import test from 'node:test';
import { connectWorkspace, createWorkspace } from '../src/workspace.mjs';

function projectRow(overrides = {}) {
  return {
    id: 'project-1',
    owner_id: 'user-1',
    organization_id: null,
    name: 'Platform',
    repository: 'acme/platform',
    description: null,
    status: 'active',
    priority: 'medium',
    is_pinned: false,
    sort_order: 0,
    last_reviewed_at: null,
    next_review_at: null,
    ...overrides,
  };
}

function mockClient({ rows = [], savedRow = projectRow() } = {}) {
  const calls = [];
  const client = {
    calls,
    auth: {
      getSession: async () => ({ data: { session: null }, error: null }),
      getUser: async () => ({ data: { user: null }, error: null }),
      signInWithPassword: async credentials => ({ data: credentials, error: null }),
      signUp: async credentials => ({ data: credentials, error: null }),
      signOut: async () => ({ data: null, error: null }),
      onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
    },
    from(table) {
      const request = { table };
      calls.push(request);
      const builder = {
        select(fields) { request.fields = fields; return builder; },
        order(column) { (request.orders ??= []).push(column); return builder; },
        insert(values) { request.insert = values; return builder; },
        update(values) { request.update = values; return builder; },
        eq(column, value) { request.filter = [column, value]; return builder; },
        single: async () => ({ data: savedRow, error: null }),
        then(resolve, reject) {
          return Promise.resolve({ data: rows, error: null }).then(resolve, reject);
        },
      };
      return builder;
    },
  };
  return client;
}

test('connectWorkspace accepts a public key and creates the configured client', async () => {
  let created;
  const workspace = await connectWorkspace({
    loadConfig: async () => ({
      ok: true,
      json: async () => ({ url: 'https://example.supabase.co', publicKey: 'sb_publishable_test_key' }),
    }),
    loadSDK: async () => ({
      createClient: (url, key) => {
        created = { url, key };
        return mockClient();
      },
    }),
  });

  assert.deepEqual(created, {
    url: 'https://example.supabase.co/',
    key: 'sb_publishable_test_key',
  });
  assert.equal(typeof workspace.listProjects, 'function');
});

test('listProjects maps persisted rows into the UI model', async () => {
  const workspace = createWorkspace(mockClient({
    rows: [projectRow({ status: 'rollback_required', is_pinned: true })],
  }));

  assert.deepEqual(await workspace.listProjects(), [{
    id: 'project-1',
    ownerId: 'user-1',
    organizationId: null,
    name: 'Platform',
    repo: 'acme/platform',
    desc: '',
    status: 'Rollback Required',
    priority: 'medium',
    pinned: true,
    order: 0,
    lastReviewed: '—',
    nextReview: '—',
  }]);
});

test('saveProject trims fields and assigns new projects to the signed-in user', async () => {
  const client = mockClient({ savedRow: projectRow({ name: 'Platform' }) });
  const workspace = createWorkspace(client);

  await workspace.saveProject({ id: 'user-1' }, {
    name: ' Platform ',
    repo: ' acme/platform ',
    desc: ' ',
    status: 'Active',
    priority: 'medium',
    pinned: false,
  });

  assert.equal(client.calls[0].table, 'projects');
  assert.deepEqual(client.calls[0].insert, {
    name: 'Platform',
    repository: 'acme/platform',
    description: null,
    status: 'active',
    priority: 'medium',
    is_pinned: false,
    owner_id: 'user-1',
  });
});

test('saveProject rejects invalid statuses before sending a database request', async () => {
  const client = mockClient();
  const workspace = createWorkspace(client);

  await assert.rejects(
    workspace.saveProject({ id: 'user-1' }, {
      name: 'Platform',
      repo: '',
      desc: '',
      status: 'Unknown',
      priority: 'medium',
      pinned: false,
    }),
    /Choose a valid project status/,
  );
  assert.equal(client.calls.length, 0);
});

test('listProjects reports unknown database statuses instead of hiding them', async () => {
  const workspace = createWorkspace(mockClient({ rows: [projectRow({ status: 'unexpected' })] }));

  await assert.rejects(workspace.listProjects(), /Unknown project status returned by Supabase: unexpected/);
});

test('createPipeline stores validated organization-owned pipeline metadata', async () => {
  const client = mockClient();
  const workspace = createWorkspace(client);

  await workspace.createPipeline('org-1', {
    name: ' Build ',
    repo: ' acme/platform ',
    branch: 'main',
    workflowId: 'deploy.yml',
    provider: 'GitHub Actions',
    hasWorkflowDispatch: true,
  });

  assert.deepEqual(client.calls[0].insert, {
    organization_id: 'org-1',
    project_id: null,
    name: 'Build',
    provider: 'GitHub Actions',
    repo: 'acme/platform',
    branch: 'main',
    workflow_id: 'deploy.yml',
    has_workflow_dispatch: true,
  });
});

test('createPipeline rejects repository values outside owner/repository form', async () => {
  const client = mockClient();
  const workspace = createWorkspace(client);

  await assert.rejects(
    workspace.createPipeline('org-1', {
      name: 'Build',
      repo: 'https://example.com/acme/platform',
      branch: 'main',
      workflowId: 'deploy.yml',
      provider: 'GitHub Actions',
      hasWorkflowDispatch: true,
    }),
    /owner\/repository format/,
  );
  assert.equal(client.calls.length, 0);
});

test('demo workspace allows sign-in without live Supabase configuration', async () => {
  const { createDemoWorkspace } = await import('../src/workspace.mjs');
  const workspace = createDemoWorkspace();

  const result = await workspace.signIn('demo@example.com', 'demo-password');

  assert.equal(result.user.email, 'demo@example.com');
  assert.equal(workspace.demoMode, true);
  assert.equal((await workspace.listProjects())[0].name, 'Checkout Platform');
});
