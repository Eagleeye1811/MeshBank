import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { exec } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(__dirname, '..')

function meshControlPlugin() {
  return {
    name: 'mesh-canary-control-api',
    configureServer(server) {
      server.middlewares.use('/api/mesh/canary-split', (req, res, next) => {
        if (req.method === 'GET') {
          exec('kubectl get configmap payment-router-config -n meshbank -o jsonpath="{.data.nginx\\\\.conf}"', { cwd: rootDir }, (err, stdout) => {
            if (err) {
              res.statusCode = 200
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ stable: 90, canary: 10, source: 'fallback' }))
              return
            }
            const sMatch = stdout.match(/server payment-service-stable[^\n]*weight=(\d+)/)
            const cMatch = stdout.match(/server payment-service-canary[^\n]*weight=(\d+)/)
            const isStableDown = stdout.includes('server payment-service-stable.meshbank.svc.cluster.local:8004 down;')
            const isCanaryDown = stdout.includes('server payment-service-canary.meshbank.svc.cluster.local:8004 down;')
            
            const stable = isStableDown ? 0 : (sMatch ? parseInt(sMatch[1], 10) : 100)
            const canary = isCanaryDown ? 0 : (cMatch ? parseInt(cMatch[1], 10) : 0)

            res.statusCode = 200
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ stable, canary, source: 'cluster' }))
          })
          return
        }

        if (req.method === 'POST') {
          let body = ''
          req.on('data', chunk => { body += chunk })
          req.on('end', () => {
            try {
              const { stable, canary } = JSON.parse(body || '{}')
              const scriptPath = path.join(rootDir, 'k8s', 'canary-traffic.py')
              const cmd = `python "${scriptPath}" --split ${stable}/${canary}`
              
              exec(cmd, { cwd: rootDir }, (err, stdout, stderr) => {
                res.setHeader('Content-Type', 'application/json')
                if (err) {
                  res.statusCode = 500
                  res.end(JSON.stringify({ error: err.message, stderr }))
                } else {
                  res.statusCode = 200
                  res.end(JSON.stringify({ success: true, stable, canary, output: stdout }))
                }
              })
            } catch (e) {
              res.statusCode = 400
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error: e.message }))
            }
          })
          return
        }

        next()
      })
    }
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), meshControlPlugin()],
})

