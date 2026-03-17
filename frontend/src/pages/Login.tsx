import { useState } from 'react';
import { TextInput, PasswordInput, Button } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faLock } from '@fortawesome/free-solid-svg-icons';

interface LoginPageProps {
  onLogin: () => void;
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (res.ok && data.authenticated) {
        onLogin();
      } else {
        notifications.show({ title: 'Login Failed', message: data.message || 'Invalid credentials', color: 'red' });
      }
    } catch {
      notifications.show({ title: 'Error', message: 'Connection failed', color: 'red' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg-primary)',
    }}>
      <form onSubmit={handleSubmit} style={{
        width: 380,
        padding: 32,
        borderRadius: 16,
        background: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
        boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div style={{
            width: 56, height: 56, borderRadius: 14, margin: '0 auto 16px',
            background: 'linear-gradient(135deg, #8b3dff, #6a1bfa)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 22, color: '#fff',
          }}>
            <FontAwesomeIcon icon={faLock} />
          </div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>
            Lyrarr
          </h2>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>
            Sign in to continue
          </p>
        </div>

        <TextInput
          label="Username"
          placeholder="Username"
          value={username}
          onChange={(e) => setUsername(e.currentTarget.value)}
          mb="md"
          autoFocus
          styles={{
            input: { background: 'rgba(25,20,50,0.4)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' },
            label: { color: 'var(--text-secondary)', marginBottom: 4 },
          }}
        />

        <PasswordInput
          label="Password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
          mb="xl"
          styles={{
            input: { background: 'rgba(25,20,50,0.4)', border: '1px solid var(--card-border)', color: 'var(--text-primary)' },
            label: { color: 'var(--text-secondary)', marginBottom: 4 },
            innerInput: { color: 'var(--text-primary)' },
          }}
        />

        <Button
          type="submit"
          fullWidth
          variant="gradient"
          gradient={{ from: '#8b3dff', to: '#6a1bfa' }}
          loading={loading}
          size="md"
        >
          Sign In
        </Button>
      </form>
    </div>
  );
}
