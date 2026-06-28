import { z } from 'zod';

// Shared Zod schemas for the auth forms. Kept deliberately permissive on the client (the
// backend is the authority on password policy and email uniqueness) while still catching the
// obvious mistakes before a network round-trip.
export const loginSchema = z.object({
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
});

export const registerSchema = z.object({
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
  password: z
    .string()
    .min(8, 'Password must be at least 8 characters'),
});
