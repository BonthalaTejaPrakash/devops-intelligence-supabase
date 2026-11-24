#!/bin/bash
# Pre-pull + Supabase link + push
git pull origin main
npx supabase link --project-ref $SUPABASE_PROJECT_REF -p $SUPABASE_DB_PASSWORD
npx supabase db push
npx supabase functions deploy send-notification --no-verify-jwt
echo 'Supabase connected'
