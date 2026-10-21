import { readFileSync } from "node:fs";
import { request, type APIRequestContext } from "@playwright/test";
import { STATE_FILE } from "../global-setup";

export const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000";

export interface Owner {
  email: string;
  password: string;
  org: string;
}

export function owner(): Owner {
  return JSON.parse(readFileSync(STATE_FILE, "utf8")) as Owner;
}

/** An API session with the owner's cookies; every unsafe call carries the double-submit CSRF header. */
export class Api {
  private constructor(
    readonly http: APIRequestContext,
    private csrf: string,
  ) {}

  static async signIn(who: Owner): Promise<Api> {
    const http = await request.newContext({ baseURL: API_URL });
    const api = new Api(http, await Api.token(http));
    const login = await api.post("/api/auth/login", { email: who.email, password: who.password, org_slug: who.org });
    if (!login.ok()) throw new Error(`login failed: ${login.status()} ${await login.text()}`);
    api.csrf = await Api.token(http);
    return api;
  }

  static async anonymous(): Promise<Api> {
    const http = await request.newContext({ baseURL: API_URL });
    return new Api(http, await Api.token(http));
  }

  private static async token(http: APIRequestContext): Promise<string> {
    const body = (await (await http.get("/api/auth/csrf")).json()) as { csrf_token: string };
    return body.csrf_token;
  }

  get(path: string) {
    return this.http.get(path);
  }

  post(path: string, data: unknown) {
    return this.http.post(path, { data, headers: { "X-CSRF-Token": this.csrf } });
  }

  delete(path: string) {
    return this.http.delete(path, { headers: { "X-CSRF-Token": this.csrf } });
  }

  async json<T>(path: string): Promise<T> {
    const response = await this.get(path);
    if (!response.ok()) throw new Error(`${path}: ${response.status()} ${await response.text()}`);
    return (await response.json()) as T;
  }

  async until<T>(path: string, done: (body: T) => boolean, seconds = 60): Promise<T> {
    const deadline = Date.now() + seconds * 1000;
    let last: T | undefined;
    while (Date.now() < deadline) {
      last = await this.json<T>(path);
      if (done(last)) return last;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(`${path} did not reach the expected state: ${JSON.stringify(last).slice(0, 500)}`);
  }
}
