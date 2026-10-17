import { describe, expect, it } from "vitest";

import { ApiError, problemMessage, unwrap } from "./problem";

describe("problem helpers", () => {
  it("prefers validation errors, then detail, then title", () => {
    expect(
      problemMessage({
        type: "urn:intrv:problem:validation-error",
        title: "Request validation failed",
        status: 422,
        errors: [{ loc: ["body", "email"], msg: "value is not a valid email address" }],
      }),
    ).toBe("email: value is not a valid email address");
    expect(problemMessage({ type: "t", title: "Forbidden", status: 403, detail: "Owners only." })).toBe("Owners only.");
    expect(problemMessage({ type: "t", title: "Forbidden", status: 403 })).toBe("Forbidden");
    expect(problemMessage("nonsense")).toBe("Something went wrong. Please try again.");
  });

  it("unwrap returns data or throws ApiError with the problem", () => {
    expect(unwrap({ data: { a: 1 }, response: new Response(null, { status: 200 }) })).toEqual({ a: 1 });
    expect(unwrap({ data: undefined, response: new Response(null, { status: 204 }) })).toBeUndefined();
    try {
      unwrap({
        error: { type: "t", title: "Conflict", status: 409, detail: "Taken." },
        response: new Response(null, { status: 409 }),
      });
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).problem.status).toBe(409);
      expect((error as Error).message).toBe("Taken.");
    }
  });
});
