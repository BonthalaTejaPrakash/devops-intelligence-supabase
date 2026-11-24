// DevOps Intelligence — Supabase Client (Frontend — SAFE keys only)
// NEVER put SUPABASE_SERVICE_ROLE_KEY here. Only ANON / PUBLISHABLE key.
// Get these from: Supabase Dashboard → Project Settings → API
import { createClient } from '@supabase/supabase-js'

export const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || 'https://YOUR_PROJECT_REF.supabase.co'
export const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || 'YOUR_SUPABASE_ANON_KEY'

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
  },
})

// Example usage in DevOps Intelligence:
// const { data: pipelines } = await supabase.from('pipelines').select('*').eq('organization_id', orgId)
// const { data: runs } = await supabase.from('pipeline_runs').select('*').order('started_at', { ascending: false }).limit(50)
// Realtime: supabase.channel('runs').on('postgres_changes', { event: '*', schema: 'public', table: 'pipeline_runs' }, payload => console.log(payload)).subscribe()
// Auth: await supabase.auth.signInWithOAuth({ provider: 'google' }) // requires email_verified check in backend
// Storage (logs): await supabase.storage.from('pipeline-logs').upload(`logs/${runId}.log`, logFile)
