import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SearchPage } from "../SearchPage";

const FIELDS = [
  "Фамилия", "Имя", "Отчество", "Пол", "Дата рождения", "Идентификатор",
  "ИНН", "Мобильный", "Рабочий", "Домашний", "Гражданство",
  "Национальность", "Адрес",
];

function searchResp(over: Partial<Record<string, unknown>> = {}) {
  return new Response(
    JSON.stringify({
      results: [], total: 0, count: 0, mode: "none", error: null,
      fields: FIELDS, page: 1, page_size: 50, ...over,
    }),
    { status: 200 },
  );
}

afterEach(() => vi.restoreAllMocks());

describe("SearchPage", () => {
  it("стартовая подсказка", () => {
    render(<SearchPage />);
    expect(screen.getByText(/Введите данные для поиска/i)).toBeInTheDocument();
  });

  it("находит запись с подсветкой и бейджем fuzzy", async () => {
    // mockImplementation -> свежий Response на каждый fetch (тело читается один раз)
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        searchResp({
          results: [{ _rowid: 1, Фамилия: "СЫЗДЫКОВА", Имя: "БАГЫЖАН", Отчество: "РЫСБАЕВНА", Адрес: "Астана" }],
          total: 1, count: 1, mode: "fuzzy",
        }),
      ),
    );
    render(<SearchPage />);
    // ФИО-поле без автодополнения -> не триггерит suggest
    fireEvent.change(screen.getByPlaceholderText("ФИО / ИИН / телефон одной строкой"), {
      target: { value: "Сыздыкова" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Найти/i }));

    await waitFor(() => expect(screen.getByText(/Найдено/i)).toBeInTheDocument());
    expect(screen.getByText(/неточное совпадение/i)).toBeInTheDocument();
    // подсветка: есть <mark>
    expect(document.querySelectorAll("mark").length).toBeGreaterThan(0);
  });

  it("показывает «ничего не найдено»", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(searchResp()));
    render(<SearchPage />);
    fireEvent.change(screen.getByPlaceholderText("ФИО / ИИН / телефон одной строкой"), {
      target: { value: "Кого-нет" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Найти/i }));
    await waitFor(() => expect(screen.getByText(/Ничего не найдено/i)).toBeInTheDocument());
  });

  it("показывает пагинацию при множестве страниц", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        searchResp({
          results: [{ _rowid: 1, Фамилия: "ИВАНОВ" }],
          total: 120, count: 1, mode: "exact",
        }),
      ),
    );
    render(<SearchPage />);
    fireEvent.change(screen.getByPlaceholderText("ФИО / ИИН / телефон одной строкой"), {
      target: { value: "Иванов" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Найти/i }));
    await waitFor(() => expect(screen.getByText(/Стр. 1 из 3/i)).toBeInTheDocument());
  });
});
