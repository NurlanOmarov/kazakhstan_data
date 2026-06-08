import { describe, it, expect, vi, afterEach } from "vitest";
import { searchResidents } from "../api";

describe("searchResidents", () => {
  afterEach(() => vi.restoreAllMocks());

  it("отправляет только непустые параметры", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ results: [], total: 0, mode: "none" }), {
          status: 200,
        }),
      );

    await searchResidents({ surname: " Иванов ", name: "", inn: "" });

    const url = fetchMock.mock.calls[0][0] as string;
    const qs = new URLSearchParams(url.split("?")[1]);
    expect(qs.get("surname")).toBe("Иванов"); // обрезка пробелов
    expect(qs.has("name")).toBe(false);
    expect(qs.has("inn")).toBe(false);
  });

  it("бросает ошибку при не-200 ответе", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("err", { status: 500 }),
    );
    await expect(searchResidents({ surname: "x" })).rejects.toThrow(/500/);
  });
});
