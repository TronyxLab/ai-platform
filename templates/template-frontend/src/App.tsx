import { useEffect, useState } from 'react'

interface HealthStatus {
  status: string
  service: string
}

function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/health')
      .then(res => res.json())
      .then(setHealth)
      .catch(err => setError(err.message))
  }, [])

  return (
    <div style={{ fontFamily: 'system-ui, sans-serif', padding: '2rem' }}>
      <h1>{{PROJECT_NAME}}</h1>
      <p>Frontend project — powered by ai-platform</p>
      <hr />
      <h2>Health</h2>
      {error && <p style={{ color: 'red' }}>Error: {error}</p>}
      {health && (
        <pre>{JSON.stringify(health, null, 2)}</pre>
      )}
      <hr />
      <h2>Platform Services</h2>
      <p>Available via <code>.env.platform</code> — run <code>make sync-env</code></p>
      <ul>
        <li>LiteLLM: <code>PLATFORM_LITELLM_URL</code></li>
        <li>Langfuse: <code>PLATFORM_LANGFUSE_URL</code></li>
      </ul>
    </div>
  )
}

export default App
