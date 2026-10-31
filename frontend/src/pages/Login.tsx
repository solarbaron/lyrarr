import { useState } from 'react';
import { TextInput, PasswordInput, Button } from '@mantine/core';
import { authLogin } from '../api';
import { errorMessage } from '../notify';
import { Banner, Panel } from '../components/ui';
import { BrandMark } from '../components/shell/BrandMark';
import c from './Login.module.css';

interface LoginPageProps {
  onLogin: () => void;
}

/** Sign-in screen for form auth (DESIGN.md 8.12). Errors show inline under the fields. */
export default function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ title: string; message: string } | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const data = await authLogin(username, password);
      if (data?.authenticated) {
        onLogin();
      } else {
        setError({ title: 'Login failed', message: data?.message || 'Invalid credentials' });
      }
    } catch (err: any) {
      if (err?.response) {
        // 401 (bad credentials) / 429 (rate limited) carry a JSON message.
        setError({ title: 'Login failed', message: errorMessage(err, 'Invalid credentials') });
      } else {
        setError({ title: 'Connection failed', message: 'Lyrarr couldn’t be reached. Check that the server is running.' });
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className={c.screen}>
      <Panel className={c.card}>
        <form onSubmit={handleSubmit} className={c.form} aria-labelledby="login-title" noValidate>
          <div className={c.brand}>
            <BrandMark size={40} />
            <h1 id="login-title" className={c.title}>Sign in to Lyrarr</h1>
          </div>

          <TextInput
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.currentTarget.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            required
          />

          <PasswordInput
            label="Password"
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
            autoComplete="current-password"
            required
          />

          {error && <Banner tone="danger" title={error.title} message={error.message} className={c.error} />}

          <Button type="submit" variant="filled" fullWidth loading={loading} className={c.submit}>
            Sign in
          </Button>
        </form>
      </Panel>
    </main>
  );
}
