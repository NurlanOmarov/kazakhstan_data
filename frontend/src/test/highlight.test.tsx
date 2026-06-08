import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { highlight } from "../highlight";

describe("highlight", () => {
  it("подсвечивает совпавший токен", () => {
    const { container } = render(<div>{highlight("СЫЗДЫКОВА БАГЫЖАН", ["Сызд"])}</div>);
    const marks = container.querySelectorAll("mark");
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toBe("СЫЗД");
  });

  it("учитывает казахско-русские варианты букв", () => {
    // запрос «Омир» должен подсветить «ӨМІР» (Ө→О, І→И)
    const { container } = render(<div>{highlight("ӨМІР", ["Омир"])}</div>);
    expect(container.querySelectorAll("mark").length).toBe(1);
  });

  it("без совпадений возвращает исходный текст", () => {
    const { container } = render(<div>{highlight("ИВАНОВ", ["Петров"])}</div>);
    expect(container.querySelectorAll("mark").length).toBe(0);
    expect(container.textContent).toBe("ИВАНОВ");
  });
});
