import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { loginSchema } from '../lib/authSchemas';
import { useAuthStore } from '../store/auth';
import { ApiError } from '../lib/api';
import Field from '../components/Field.jsx';

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const login = useAuthStore((s) => s.login);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [serverError, setServerError] = useState(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm({ resolver: zodResolver(loginSchema), defaultValues: { email: '', password: '' } });

  // Where to land after login: the page the user was bounced from, or the dashboard.
  const from = location.state?.from?.pathname || '/';

  // Already signed in? Skip the form.
  if (isAuthenticated) {
    return <Navigate to={from} replace />;
  }

  const onSubmit = async ({ email, password }) => {
    setServerError(null);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setServerError('Invalid email or password.');
      } else if (err instanceof ApiError && err.status === 429) {
        setServerError('Too many attempts. Please wait a moment and try again.');
      } else {
        setServerError('Something went wrong. Please try again.');
      }
    }
  };

  return (
    <div className="card p-6 sm:p-8">
      <h1 className="text-xl font-semibold text-ink-900">Welcome back</h1>
      <p className="mt-1 text-sm text-ink-500">Sign in to your contracts and deadlines.</p>

      <form className="mt-6 space-y-4" onSubmit={handleSubmit(onSubmit)} noValidate>
        {serverError ? (
          <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-risk-critical">
            {serverError}
          </div>
        ) : null}

        <Field
          id="email"
          label="Email"
          type="email"
          autoComplete="email"
          registration={register('email')}
          error={errors.email}
        />
        <Field
          id="password"
          label="Password"
          type="password"
          autoComplete="current-password"
          registration={register('password')}
          error={errors.password}
        />

        <button type="submit" disabled={isSubmitting} className="btn-primary w-full">
          {isSubmitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <p className="mt-5 text-sm text-ink-500">
        No account?{' '}
        <Link to="/register" className="font-semibold text-brand-700 hover:text-brand-800">
          Create one
        </Link>
      </p>
    </div>
  );
}
