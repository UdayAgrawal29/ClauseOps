import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import { registerSchema } from '../lib/authSchemas';
import { useAuthStore } from '../store/auth';
import { ApiError } from '../lib/api';
import Field from '../components/Field.jsx';

export default function Register() {
  const navigate = useNavigate();
  const registerUser = useAuthStore((s) => s.register);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const [serverError, setServerError] = useState(null);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: '', password: '' },
  });

  // Already signed in? Skip the form.
  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  const onSubmit = async ({ email, password }) => {
    setServerError(null);
    try {
      await registerUser(email, password);
      navigate('/', { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError('email', {
          type: 'server',
          message: 'An account with this email already exists.',
        });
      } else if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
        setServerError(
          typeof err.message === 'string'
            ? err.message
            : 'Please check your details and try again.',
        );
      } else {
        setServerError('Something went wrong. Please try again.');
      }
    }
  };

  return (
    <div className="card p-6 sm:p-8">
      <h1 className="text-xl font-semibold text-ink-900">Create your account</h1>
      <p className="mt-1 text-sm text-ink-500">Start tracking your contract deadlines.</p>

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
          autoComplete="new-password"
          registration={register('password')}
          error={errors.password}
        />

        <button type="submit" disabled={isSubmitting} className="btn-primary w-full">
          {isSubmitting ? 'Creating account…' : 'Create account'}
        </button>
      </form>

      <p className="mt-5 text-sm text-ink-500">
        Already have an account?{' '}
        <Link to="/login" className="font-semibold text-brand-700 hover:text-brand-800">
          Sign in
        </Link>
      </p>
    </div>
  );
}
