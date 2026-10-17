/** RFC 7807 problem details returned by the backend for every error. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  request_id?: string | null;
  errors?: { loc: (string | number)[]; msg: string }[];
  problems?: string[];
  organizations?: { slug: string; name: string }[];
  [key: string]: unknown;
}

export function isProblem(value: unknown): value is Problem {
  return typeof value === "object" && value !== null && "type" in value && "status" in value;
}

/** A human-readable message for any error thrown or returned by the API client. */
export function problemMessage(error: unknown, fallback = "Something went wrong. Please try again."): string {
  if (isProblem(error)) {
    if (error.errors?.length) {
      return error.errors.map((e) => `${e.loc.filter((part) => part !== "body").join(".")}: ${e.msg}`).join("; ");
    }
    return error.detail ?? error.title ?? fallback;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export class ApiError extends Error {
  constructor(readonly problem: Problem) {
    super(problemMessage(problem));
    this.name = "ApiError";
  }
}

/** Unwrap an openapi-fetch result: return data or throw ApiError with the problem details. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || result.data === undefined) {
    if (result.response.status === 204) return undefined as T;
    const problem = isProblem(result.error)
      ? result.error
      : { type: "about:blank", title: "Request failed", status: result.response.status };
    throw new ApiError(problem);
  }
  return result.data;
}
