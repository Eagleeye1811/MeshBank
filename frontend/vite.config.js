import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(__dirname, '..')

const DEV_PORT = 5173
// Pages the dashboard is served from. Any other Origin is refused, so another
// website open in the same browser cannot drive these endpoints.
const ALLOWED_ORIGINS = new Set([`http://localhost:${DEV_PORT}`, `http://127.0.0.1:${DEV_PORT}`])
// Matches the manifest default in k8s/mesh-router-config.yaml.
const DEFAULT_SPLIT = { stable: 80, canary: 20 }
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const AMOUNT_RE = /^\d{1,9}(\.\d{1,2})?$/

// The internal token stays on the dev server; it is never sent to the browser.
// Default: the built-in development token used by `make run` and Docker Compose.
const INTERNAL_API_TOKEN =
  process.env.MESHBANK_INTERNAL_API_TOKEN ||
  'dev-only-insecure-internal-token-do-not-use-in-production'
const LOCAL_ACCOUNT_URL = process.env.MESHBANK_ACCOUNT_URL || 'http://127.0.0.1:8002'

// Which kind cluster runs a service (mirrors kube_context in scripts/mesh_topology.py).
// MESHBANK_TOPOLOGY=multi: payment in meshbank-gcp, account in meshbank-aws.
const TOPOLOGY = process.env.MESHBANK_TOPOLOGY === 'multi' ? 'multi' : 'single'
const KUBE_CONTEXT = TOPOLOGY === 'multi'
  ? { payment: 'kind-meshbank-gcp', account: 'kind-meshbank-aws' }
  : { payment: 'kind-meshbank', account: 'kind-meshbank' }

function pythonBin() {
  const venv = path.join(rootDir, '.venv', 'bin', 'python')
  return fs.existsSync(venv) ? venv : 'python3'
}

function sendJson(res, status, body) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(body))
}

// Same-origin JSON requests only. Requiring application/json forces a CORS
// preflight for cross-site requests, and the Origin check rejects them.
function rejectForeign(req, res) {
  const origin = req.headers.origin
  if (origin && !ALLOWED_ORIGINS.has(origin)) {
    sendJson(res, 403, { error: 'origin not allowed' })
    return true
  }
  if (req.method === 'POST' && !(req.headers['content-type'] || '').startsWith('application/json')) {
    sendJson(res, 415, { error: 'content-type must be application/json' })
    return true
  }
  return false
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', chunk => {
      body += chunk
      if (body.length > 10_000) reject(new Error('body too large'))
    })
    req.on('end', () => {
      try {
        resolve(JSON.parse(body || '{}'))
      } catch (e) {
        reject(e)
      }
    })
  })
}

function isWeight(value) {
  return Number.isInteger(value) && value >= 0 && value <= 100
}

function meshControlPlugin() {
  return {
    name: 'mesh-canary-control-api',
    configureServer(server) {
      // Which deployment the dashboard is pointed at (no secrets).
      server.middlewares.use('/api/mesh/info', (req, res, next) => {
        if (req.method !== 'GET') return next()
        if (rejectForeign(req, res)) return
        sendJson(res, 200, { topology: TOPOLOGY, contexts: KUBE_CONTEXT })
      })

      server.middlewares.use('/api/mesh/canary-split', async (req, res, next) => {
        if (rejectForeign(req, res)) return

        if (req.method === 'GET') {
          const args = ['--context', KUBE_CONTEXT.payment, 'get', 'configmap', 'payment-router-config', '-n', 'meshbank',
            '-o', 'jsonpath={.data.nginx\\.conf}']
          execFile('kubectl', args, { cwd: rootDir, timeout: 10_000 }, (err, stdout) => {
            if (err) {
              sendJson(res, 200, { ...DEFAULT_SPLIT, source: 'fallback' })
              return
            }
            const weight = track => {
              if (new RegExp(`server payment-service-${track}[^\\n]* down;`).test(stdout)) return 0
              const m = stdout.match(new RegExp(`server payment-service-${track}[^\\n]*weight=(\\d+)`))
              return m ? parseInt(m[1], 10) : null
            }
            const stable = weight('stable')
            const canary = weight('canary')
            sendJson(res, 200, {
              stable: stable ?? DEFAULT_SPLIT.stable,
              canary: canary ?? DEFAULT_SPLIT.canary,
              source: 'cluster',
              context: KUBE_CONTEXT.payment,
            })
          })
          return
        }

        if (req.method === 'POST') {
          let body
          try {
            body = await readJson(req)
          } catch (e) {
            sendJson(res, 400, { error: e.message })
            return
          }
          const { stable, canary } = body
          if (!isWeight(stable) || !isWeight(canary) || stable + canary === 0) {
            sendJson(res, 400, { error: 'stable and canary must be integers 0-100, not both 0' })
            return
          }
          // execFile passes arguments directly (no shell), so nothing in the
          // request can be interpreted as a command.
          const script = path.join(rootDir, 'k8s', 'canary-traffic.py')
          const args = [script, '--set-split', String(stable), String(canary)]
          execFile(pythonBin(), args, { cwd: rootDir, timeout: 120_000 }, (err, stdout, stderr) => {
            if (err) {
              sendJson(res, 500, { error: 'set-split failed', stderr, output: stdout })
            } else {
              sendJson(res, 200, { success: true, stable, canary, output: stdout })
            }
          })
          return
        }

        next()
      })

      // Development-only funding. Kubernetes: runs the account service's own
      // dev_seed CLI inside its pod (its /internal endpoints are mTLS-only).
      // Local / Docker: calls the dev endpoint with the server-side token.
      server.middlewares.use('/api/dev/seed', async (req, res, next) => {
        if (req.method !== 'POST') return next()
        if (rejectForeign(req, res)) return
        let body
        try {
          body = await readJson(req)
        } catch (e) {
          sendJson(res, 400, { error: e.message })
          return
        }
        const { env, accountId, amount } = body
        if (!['k8s', 'docker'].includes(env) || !UUID_RE.test(accountId || '') || !AMOUNT_RE.test(amount || '')) {
          sendJson(res, 400, { error: 'env must be k8s|docker, accountId a UUID, amount like 5000.00' })
          return
        }

        if (env === 'k8s') {
          const args = ['--context', KUBE_CONTEXT.account, 'exec', '-n', 'meshbank',
            'deployment/account-service', '-c', 'account-service', '--',
            'python', '-m', 'app.dev_seed', '--account-id', accountId, '--amount', amount]
          execFile('kubectl', args, { cwd: rootDir, timeout: 30_000 }, (err, stdout, stderr) => {
            if (err) sendJson(res, 502, { error: (stderr || err.message).trim() })
            else sendJson(res, 200, { success: true, output: stdout.trim() })
          })
          return
        }

        try {
          const upstream = await fetch(`${LOCAL_ACCOUNT_URL}/internal/dev/accounts/${accountId}/seed`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Internal-Token': INTERNAL_API_TOKEN },
            body: JSON.stringify({ amount }),
            signal: AbortSignal.timeout(10_000),
          })
          const data = await upstream.json().catch(() => ({}))
          sendJson(res, upstream.status, data)
        } catch (e) {
          sendJson(res, 502, { error: e.message })
        }
      })
    }
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), meshControlPlugin()],
  server: { port: DEV_PORT, strictPort: true },
})
