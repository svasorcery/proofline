import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

// One maintained Markdown source; standalone HTML and SVG need only Node.js.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dir = path.join(root, "docs/product");
const assets = path.join(dir, "assets");
await mkdir(assets, { recursive: true });

const c = {
  paper: "#f6f3eb",
  ink: "#142b35",
  muted: "#4e6367",
  line: "#b5c1bb",
  teal: "#14655c",
  pale: "#e4eee6",
  blue: "#275b8c",
  bluePale: "#e5edf5",
  rust: "#a34a2f",
  rustPale: "#f5e6d9",
  white: "#fffefb",
};
const esc = (s) =>
  String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
const text = (x, y, s, size = 22, fill = c.ink, weight = 400, extra = "") =>
  `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}" ${extra}>${esc(s)}</text>`;
const lines = (x, y, list, size = 21, fill = c.muted, gap = 29) =>
  list.map((s, i) => text(x, y + i * gap, s, size, fill)).join("");
const rect = (x, y, w, h, fill, r = 14) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}"/>`;
const arrow = (x1, y1, x2, y2) =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c.line}" stroke-width="2" marker-end="url(#arrow)"/>`;
const label = (x, y, s) =>
  text(x, y, s, 16, c.muted, 600, 'letter-spacing="1.5"');
const node = (x, y, w, h, title, body, fill = c.white, accent = c.teal) =>
  `<g class="product-node" data-x="${x}" data-y="${y}" data-width="${w}" data-height="${h}">${rect(x, y, w, h, fill)}${rect(x, y + 19, 4, h - 38, accent, 2)}${text(x + 24, y + 39, title, 26, c.ink, 600)}${lines(x + 24, y + 73, body, 20, c.muted, 28)}</g>`;
const svg = (w, h, title, desc, body) =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-labelledby="title desc"><title id="title">${esc(title)}</title><desc id="desc">${esc(desc)}</desc><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${c.line}"/></marker></defs><g font-family="Segoe UI, Arial, sans-serif">${rect(0, 0, w, h, c.paper, 0)}${body}</g></svg>\n`;

const map =
  label(48, 44, "PROOFLINE / КАРТА ПРОДУКТА") +
  text(48, 103, "От обращения к проверенному разбору", 43, c.ink, 600) +
  text(
    48,
    145,
    "Оператор видит вывод, его основание и то, чего ещё не хватает.",
    23,
    c.muted,
  ) +
  node(
    48,
    184,
    1104,
    116,
    "Обращение и выбранные источники",
    [
      "Письма клиента · ответы поставщика · журналы событий · банковские записи",
    ],
    c.pale,
  ) +
  arrow(316, 302, 316, 333) +
  node(
    48,
    340,
    536,
    164,
    "AI готовит разбор",
    [
      "Факты и подтверждающие фрагменты",
      "События со временем и его смыслом",
      "Недостающие сведения и следующий шаг",
    ],
    c.bluePale,
    c.blue,
  ) +
  node(616, 340, 536, 164, "Приложение проверяет", [
    "Форму ответа и принадлежность источников",
    "Дословность и положение фрагментов",
    "Заданные правила согласованности",
  ]) +
  arrow(585, 422, 612, 422) +
  arrow(884, 509, 884, 540) +
  node(
    48,
    548,
    1104,
    145,
    "Оператор сопоставляет результат с документами",
    [
      "Открывает цитаты · проверяет смысл и полноту · исправляет с пояснением",
      "Подтверждает или отклоняет конкретную версию разбора",
    ],
    c.pale,
  ) +
  label(48, 744, "КАК ЭТО УСТРОЕНО") +
  node(48, 769, 536, 137, "React + TypeScript", [
    "Карточка, источники, выделение цитат",
    "Редактор, замечания и история",
  ]) +
  node(616, 769, 536, 137, "Python + FastAPI", [
    "Анализ, проверки и решения",
    "Один backend для всего процесса",
  ]) +
  node(48, 930, 536, 137, "PostgreSQL", [
    "Версии документов и разборов",
    "Результаты проверок и решения человека",
  ]) +
  node(616, 930, 536, 137, "Docker Compose", [
    "Локальный запуск приложения и базы",
    "Подготовленные учебные обращения",
  ]) +
  rect(48, 1097, 1104, 120, c.ink) +
  text(72, 1137, "Два способа получить разбор", 26, c.white, 600) +
  text(
    72,
    1175,
    "Replay: сохранённый ответ   /   Live: новый запрос к выбранной модели",
    22,
    "#e0e9e7",
  ) +
  text(
    48,
    1258,
    "OpenAI · Groq · DeepSeek · Anthropic: один провайдер на запуск, собственный API-доступ.",
    20,
    c.muted,
  ) +
  text(
    48,
    1300,
    "Документы → разбор → проверка → решение. Каждый шаг сохраняет своё происхождение.",
    21,
    c.teal,
  );

const consent =
  label(48, 44, "РАЗБОР / УСЛОВНОЕ СОГЛАСИЕ") +
  text(48, 102, "Одно слово ещё не закрывает вопрос", 43, c.ink, 600) +
  rect(48, 142, 1104, 119, c.ink) +
  text(72, 182, "Письмо клиента", 20, "#d0dfd9", 600) +
  text(
    72,
    224,
    '"Если там тихий номер с окнами во двор, согласен на соседний корпус"',
    26,
    c.white,
  ) +
  node(
    48,
    302,
    536,
    169,
    "В сохранённом ответе модели",
    [
      "Факт: согласие с условиями",
      "Событие: альтернатива принята",
      "Два утверждения расходятся",
    ],
    c.rustPale,
    c.rust,
  ) +
  node(
    616,
    302,
    536,
    169,
    "Замечание приложения",
    [
      "Условное согласие нельзя отметить",
      "как принятую альтернативу.",
      "Подтверждение разбора заблокировано.",
    ],
    c.rustPale,
    c.rust,
  ) +
  arrow(585, 386, 612, 386) +
  arrow(885, 474, 885, 511) +
  node(
    48,
    519,
    1104,
    154,
    "Правка оператора",
    [
      "Прочитать письмо → убрать преждевременное событие → уточнить запрос.",
      "Отдельно выяснить: тихий ли номер? Выходят ли окна во двор?",
      "Сохранить новую версию с причиной исправления.",
    ],
    c.pale,
  ) +
  arrow(600, 676, 600, 710) +
  node(
    48,
    718,
    1104,
    121,
    "Повторная проверка и решение человека",
    [
      "Оператор подтверждает корректность разбора или отклоняет его с пояснением.",
    ],
    c.bluePale,
    c.blue,
  ) +
  text(
    48,
    883,
    "Первоначальная ошибка остаётся в истории. Исправленная версия хранится отдельно.",
    22,
    c.muted,
  );

const history =
  label(48, 44, "ИСТОРИЯ / ДОКУМЕНТЫ И РЕШЕНИЯ") +
  text(48, 102, "Что было известно в момент решения", 43, c.ink, 600) +
  label(48, 165, "ПЕРВЫЙ РАЗБОР") +
  node(
    48,
    190,
    340,
    174,
    "Источники № 1",
    ["Письмо с условиями", "Ответа отеля пока нет", "Текст сохранён"],
    c.pale,
  ) +
  node(430, 190, 340, 174, "Разбор № 1", [
    "Согласие с условиями",
    "Нужны подтверждения",
    "Цитаты из источников № 1",
  ]) +
  node(
    812,
    190,
    340,
    174,
    "Решение № 1",
    ["Оператор проверил", "и подтвердил разбор", "Комментарий и время"],
    c.bluePale,
    c.blue,
  ) +
  arrow(390, 277, 424, 277) +
  arrow(772, 277, 806, 277) +
  rect(48, 405, 1104, 86, c.ink) +
  text(
    72,
    457,
    "Пришли новые сведения → изменился набор источников → нужен новый анализ",
    24,
    c.white,
  ) +
  label(48, 544, "ПОСЛЕ ОБНОВЛЕНИЯ МАТЕРИАЛОВ") +
  node(
    48,
    570,
    340,
    174,
    "Источники № 2",
    ["Прежние документы", "Новый ответ отеля", "Новый снимок материалов"],
    c.pale,
  ) +
  node(430, 570, 340, 174, "Разбор № 2", [
    "Анализ новых сведений",
    "Свои факты и цитаты",
    "Повторные проверки",
  ]) +
  node(
    812,
    570,
    340,
    174,
    "Решение № 2",
    ["Оператор проверяет", "новую версию", "и оставляет решение"],
    c.bluePale,
    c.blue,
  ) +
  arrow(390, 657, 424, 657) +
  arrow(772, 657, 806, 657) +
  text(
    48,
    800,
    "Старая цитата открывает старый текст. Новые сведения не изменяют прошлое решение.",
    22,
    c.muted,
  ) +
  text(
    48,
    840,
    "В истории можно восстановить основания каждого разбора.",
    22,
    c.teal,
  );

const figures = [
  [
    "proofline-map",
    1200,
    1336,
    "Proofline: карта продукта",
    "Источники, разбор AI, проверки кода и решение человека. React показывает карточку, FastAPI обслуживает процесс, PostgreSQL хранит версии. Два режима: сохранённый Replay и новый Live.",
    map,
  ],
  [
    "conditional-consent",
    1200,
    920,
    "Условное согласие: от ошибки к правке",
    "Модель отметила условное согласие и принятую альтернативу одновременно. Приложение блокирует подтверждение. Оператор сверяет письмо, исправляет событие и сохраняет новую версию.",
    consent,
  ],
  [
    "review-history",
    1200,
    880,
    "История документов и решений",
    "Первый разбор и решение связаны с первым набором источников. После обновления материалов создаётся новый анализ и отдельное решение. Старые основания остаются доступны.",
    history,
  ],
];
for (const [name, width, height, title, desc, body] of figures) {
  await writeFile(
    path.join(assets, `${name}.svg`),
    svg(width, height, title, desc, body),
  );
}

const markdown = await readFile(path.join(dir, "proofline.ru.md"), "utf8");
if (markdown.includes("\r")) {
  throw new Error("Save proofline.ru.md with LF line endings before building");
}
const slug = (s) =>
  s
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-|-$/g, "");
const embeddedAssets = new Map(
  await Promise.all(
    figures.map(async ([name]) => [
      `assets/${name}.svg`,
      `data:image/svg+xml;base64,${(await readFile(path.join(assets, `${name}.svg`))).toString("base64")}`,
    ]),
  ),
);
function url(href) {
  if (/^https:\/\//.test(href)) return href;
  if (href.startsWith("assets/")) {
    const embedded = embeddedAssets.get(href);
    if (!embedded) throw new Error(`Unknown illustration: ${href}`);
    return embedded;
  }
  const relative = path
    .relative(root, path.resolve(dir, href))
    .split(path.sep)
    .join("/");
  if (relative.startsWith("../"))
    throw new Error(`Link outside repository: ${href}`);
  const kind = !path.extname(relative) ? "tree" : "blob";
  return `https://github.com/svasorcery/proofline/${kind}/master/${relative}`;
}
function inline(s) {
  return esc(s)
    .replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      (_, label, href) =>
        `<a href="${esc(url(href))}"${embeddedAssets.has(href) ? ` download="${path.basename(href)}"` : ""}>${label}</a>`,
    )
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}
const toc = [];
const content = [];
for (const block of markdown.trim().split(/\r?\n\s*\r?\n/)) {
  if (block.startsWith("# ")) continue;
  if (block.startsWith("## ")) {
    const title = block.slice(3);
    const id = slug(title);
    toc.push({ title, id });
    content.push(
      `<h2 id="${id}"><span class="chapter">${String(toc.length).padStart(2, "0")}</span>${inline(title)}</h2>`,
    );
  } else if (block.startsWith("> ")) {
    content.push(
      `<aside class="demo-note" aria-label="Об этом проекте"><p>${inline(block.replace(/^> ?/gm, "").replaceAll("\n", " "))}</p></aside>`,
    );
  } else if (block.startsWith("![")) {
    const match = block.match(/^!\[([^\]]+)\]\((assets\/[^)]+\.svg)\)$/);
    if (!match) throw new Error("Unsupported image block");
    const data = (await readFile(path.join(dir, match[2]))).toString("base64");
    content.push(
      `<figure><a href="data:image/svg+xml;base64,${data}" download="${path.basename(match[2])}" aria-label="Сохранить схему SVG: ${esc(match[1])}"><img src="data:image/svg+xml;base64,${data}" alt="${esc(match[1])}" loading="lazy"/></a><figcaption>${esc(match[1])}</figcaption></figure>`,
    );
  } else if (block === "---") content.push("<hr/>");
  else {
    if (/^(?:[-*] |\d+\. |```|\|)/.test(block))
      throw new Error("Unsupported Markdown block");
    content.push(`<p>${inline(block.replaceAll("\n", " "))}</p>`);
  }
}
const [tagline, ...rest] = content;
const html = `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Proofline – на чём основан ответ</title>
<meta name="description" content="Proofline помогает разобрать обращение: сопоставить факты с документами, восстановить события и сохранить проверенное решение оператора."/>
<style>
:root{color-scheme:light;--paper:#f6f3eb;--ink:#142b35;--muted:#4e6367;--teal:#14655c;--line:#d3d8ce}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:28px}body{margin:0;background:var(--paper);color:var(--ink);font-family:Georgia,'Times New Roman',serif;font-size:21px;line-height:1.72}a{color:var(--teal);text-decoration-thickness:1px;text-underline-offset:4px}a:hover{text-decoration-thickness:2px}a:focus-visible,summary:focus-visible{outline:3px solid var(--teal);outline-offset:5px}header,main,footer{max-width:1180px;margin:auto;padding-left:44px;padding-right:44px}header{padding-top:48px}.masthead{display:flex;justify-content:space-between;gap:24px;align-items:center;font:600 13px/1.4 'Segoe UI',Arial,sans-serif;letter-spacing:1.8px;text-transform:uppercase;padding-bottom:22px;border-bottom:1px solid var(--line)}.masthead a{text-decoration:none}.hero{padding:72px 0 52px;max-width:900px}.eyebrow{font:600 14px/1.5 'Segoe UI',Arial,sans-serif;color:var(--teal);letter-spacing:2px;text-transform:uppercase}.hero h1{font-size:clamp(48px,6.1vw,79px);font-weight:400;line-height:1.1;letter-spacing:-2.5px;margin:25px 0 28px}.hero p{font-size:27px;line-height:1.5;max-width:760px;color:var(--muted);margin:0}.contents{border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:18px 0;margin-bottom:58px;font:16px/1.7 'Segoe UI',Arial,sans-serif}.contents summary{cursor:pointer;font-weight:600}.contents ol{columns:2;column-gap:40px;list-style:decimal-leading-zero;padding-left:28px;margin:20px 0 6px}.contents li{break-inside:avoid;padding:4px 0}main>p,main>h2,main>aside,main>hr{max-width:760px;margin-left:auto;margin-right:auto}main>p{margin-top:0;margin-bottom:24px}main>p:first-child{font-size:25px;line-height:1.6}strong{font-weight:700}.demo-note{padding:24px 28px;border-left:3px solid var(--teal);background:#e8ede4;font:16px/1.75 'Segoe UI',Arial,sans-serif;margin-top:40px;margin-bottom:68px}.demo-note p{margin:0}.demo-note strong:first-child{display:block;font-size:18px;margin-bottom:9px}main>h2{font-size:40px;font-weight:400;line-height:1.22;letter-spacing:-.8px;padding-top:38px;margin-top:48px;margin-bottom:28px;break-after:avoid}.chapter{display:block;font:600 13px/1.5 'Segoe UI',Arial,sans-serif;letter-spacing:2px;color:var(--teal);margin-bottom:14px}figure{margin:42px 0 34px}figure img{display:block;width:100%;height:auto}figcaption{font:14px/1.6 'Segoe UI',Arial,sans-serif;color:var(--muted);max-width:760px;margin:16px auto 0}main>p:has(>em:only-child){font:15px/1.6 'Segoe UI',Arial,sans-serif;color:var(--muted)}main>hr{border:0;border-top:1px solid var(--line);margin-top:60px;margin-bottom:32px}main>p:last-child{font:16px/1.9 'Segoe UI',Arial,sans-serif}footer{padding-top:38px;padding-bottom:46px;font:13px/1.5 'Segoe UI',Arial,sans-serif;color:var(--muted)}footer div{border-top:1px solid var(--line);padding-top:20px;display:flex;justify-content:space-between;gap:20px}
@media(max-width:700px){header,main,footer{padding-left:22px;padding-right:22px}header{padding-top:26px}.masthead{font-size:10px;letter-spacing:1px}.hero{padding:44px 0 36px}.hero h1{letter-spacing:-1.5px}.hero p{font-size:23px}.contents{margin-bottom:36px}.contents ol{columns:1}body{font-size:19px;line-height:1.7}main>p:first-child{font-size:22px}.demo-note{padding:20px;font-size:15px;margin-bottom:44px}main>h2{font-size:32px;padding-top:20px;margin-top:36px}figure{margin-left:-10px;margin-right:-10px}figcaption{padding:0 10px;font-size:12px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{body{background:white;font-size:12pt;line-height:1.55}header,main,footer{max-width:none;padding-left:0;padding-right:0}.hero{padding:28px 0}.hero h1{font-size:42pt}.hero p{font-size:17pt}.masthead{font-size:9pt}.contents{display:none}main>p:first-child{font-size:15pt}main>h2{font-size:25pt;padding-top:12px;margin-top:24px}.chapter{font-size:9pt}.demo-note{font-size:10pt;margin:24px auto;padding:16px}figure{break-inside:avoid;margin:22px 0}figcaption{font-size:9pt}a{color:inherit}footer{padding-bottom:0}@page{size:A4;margin:18mm}}
</style></head><body>
<header id="top"><div class="masthead"><a href="https://github.com/svasorcery/proofline">Proofline / авторский проект</a><span>Владимир Синявский · ноябрь 2026</span></div><div class="hero"><div class="eyebrow">История продукта</div><h1>На чём основан ответ</h1>${tagline}</div>
<details class="contents"><summary>Открыть оглавление · ${toc.length} глав</summary><ol>${toc.map(({ title, id }) => `<li><a href="#${id}">${esc(title)}</a></li>`).join("")}</ol></details></header>
<main>${rest.join("\n")}</main><footer><div><span>Proofline · факты, источники, решения</span><a href="#top">К началу</a></div></footer></body></html>\n`;
await writeFile(path.join(dir, "proofline.ru.html"), html);
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const publication = {
  schemaVersion: 1,
  id: "proofline",
  title: "Proofline: на чём основан ответ",
  description:
    "Как Proofline помогает оператору сопоставить факты с документами, восстановить события и сохранить проверенный разбор обращения.",
  document: { path: "proofline.ru.md", sha256: sha256(markdown) },
  assets: await Promise.all(
    figures.map(async ([name]) => ({
      path: `assets/${name}.svg`,
      sha256: sha256(await readFile(path.join(assets, `${name}.svg`))),
    })),
  ),
};
await writeFile(
  path.join(dir, "publication.json"),
  `${JSON.stringify(publication, null, 2)}\n`,
);
console.log("Built three SVG illustrations and docs/product/proofline.ru.html");

if (process.argv.includes("--png")) {
  const { chromium } =
    await import("../frontend/node_modules/playwright/index.mjs");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1200, height: 1336 },
      deviceScaleFactor: 2,
    });
    for (const [name] of figures) {
      await page.setContent(
        await readFile(path.join(assets, `${name}.svg`), "utf8"),
      );
      await page
        .locator("svg")
        .screenshot({ path: path.join(assets, `${name}.png`) });
    }
  } finally {
    await browser.close();
  }
  console.log("Exported three PNG illustrations at 2x resolution");
}
