import { z } from "zod";

/** Mirrors the backend rules (app/core/security.py); the server remains the authority. */
export const passwordSchema = z
  .string()
  .min(12, "At least 12 characters")
  .max(256, "At most 256 characters")
  .refine((value) => new Set(value).size >= 5, "Too repetitive");

export const orgSlugSchema = z
  .string()
  .trim()
  .toLowerCase()
  .regex(/^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$/, "3-64 characters: lowercase letters, digits and hyphens");

export const emailSchema = z.email("Enter a valid email address").trim().toLowerCase();
