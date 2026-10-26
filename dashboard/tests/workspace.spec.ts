import { test, expect, type Page, type BrowserContext } from "@playwright/test";
const password = "Browser-test-passphrase-2026";
let cookies:Awaited<ReturnType<BrowserContext['cookies']>>=[];
test.beforeAll(async({request})=>{
  const status=await (await request.get('/api/auth/status')).json();
  const response=await request.post(status.configured?'/api/auth/login':'/api/auth/setup',{data:{password}});
  expect(response.ok()).toBeTruthy();
  cookies=(await request.storageState()).cookies;
});
async function enter(page: Page) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Your call operations, in focus." }),
  ).toBeVisible();
}
async function nav(page: Page, name: string) {
  const open = page.getByRole("button", { name: "Open navigation" });
  if (await open.isVisible()) await open.click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name, exact: true })
    .click();
}
test.beforeEach(async ({ page,context }) => {
  await context.addCookies(cookies);
  await page.route("**/api/dial", (route) => {
    throw new Error("Browser tests must never dial");
  });
});
test("synthetic overview, history, structured result and unavailable messaging", async ({
  page,
}) => {
  await enter(page);
  await expect(page.getByText("Synthetic demo workspace.")).toBeVisible();
  await nav(page, "Call History");
  await page.getByLabel("Search calls").fill("Juniper");
  await expect(page.locator("tbody tr")).not.toHaveCount(0);
  await page.locator("tbody tr").first().click();
  await expect(
    page.getByRole("heading", { name: "Call monitor" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Results", exact: true }).click();
  await expect(
    page
      .getByText("Connection completion does not imply task success.")
      .or(page.getByText("Transcript evidence", { exact: false })),
  ).toBeVisible();
  await nav(page, "Messages (Unavailable)");
  await expect(
    page
      .getByRole("heading", { name: "Messaging is unavailable" })
      .or(page.getByText("Messaging is unavailable", { exact: true })),
  ).toBeVisible();
});
test("local contact CRUD and real API persistence", async ({ page }) => {
  await enter(page);
  await nav(page, "Contacts");
  await page
    .getByRole("button", { name: "New contact", exact: true })
    .first()
    .click();
  await page
    .getByLabel("Name", { exact: true })
    .fill("Browser Synthetic Contact");
  await page.getByLabel("Phone number").fill("+12025550199");
  await page.getByLabel("Tags").fill("Synthetic, Browser");
  await page.getByLabel("Allow outbound calls to this exact number").check();
  await page.getByRole("button", { name: "Save contact" }).click();
  await expect(
    page.getByRole("heading", { name: "Browser Synthetic Contact" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Delete Browser Synthetic Contact" })
    .click();
  await page
    .getByRole("button", { name: "Delete contact", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Browser Synthetic Contact" }),
  ).toHaveCount(0);
});
test("encrypted vault remains masked until password reauthentication", async ({
  page,
}) => {
  await enter(page);
  await nav(page, "Personal Vault");
  await page.getByRole("button", { name: "New profile" }).click();
  await page.getByLabel("Profile name").fill("Browser Synthetic Profile");
  await page.getByRole("button", { name: "Create profile" }).click();
  await page.getByRole("button", { name: "Add field", exact: true }).click();
  await page.getByLabel("Field label").fill("Membership number");
  await page.getByLabel("Category").selectOption("insurance");
  await page.getByLabel("Private value").fill("SYNTHETIC-ONLY-90812");
  await page.getByRole("button", { name: "Save encrypted field" }).click();
  await expect(
    page.getByText("SYNTHETIC-ONLY-90812", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Reveal Membership number" }).click();
  await expect(
    page.getByRole("dialog", { name: "Confirm it’s you" }),
  ).toBeVisible();
  await page.getByLabel("Operator password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Confirm & continue" }).click();
  await expect(
    page.getByText("SYNTHETIC-ONLY-90812", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Hide Membership number" }).click();
  await expect(
    page.getByText("SYNTHETIC-ONLY-90812", { exact: true }),
  ).toHaveCount(0);
});
test("responsive settings theme, all navigation, and safe preflight error", async ({
  page,
}) => {
  await enter(page);
  for (const name of [
    "Agents",
    "Providers & SIP",
    "Analytics",
    "Live Logs",
    "Automations",
    "Transcripts",
    "Live Calls",
  ]) {
    await nav(page, name);
    await expect(page.locator("h1")).toBeVisible();
  }
  await nav(page, "Settings");
  await page.getByRole("button", { name: "Daylight" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await nav(page, "Overview");
  await page
    .getByRole("button", { name: "Prepare a call", exact: true })
    .click();
  await page
    .getByLabel("Contact", { exact: true })
    .selectOption("demo_contact_0");
  await page
    .getByLabel("Agent", { exact: true })
    .selectOption("demo_concierge");
  await page.getByRole("button", { name: "Run preflight" }).click();
  await expect(page.getByRole("alert")).toContainText(
    /unavailable|not satisfied/,
  );
  await expect(
    page.getByRole("button", { name: "Confirm & dial" }),
  ).toHaveCount(0);
  const width = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }));
  expect(width.scroll).toBeLessThanOrEqual(width.client);
});

test("advertised private control uses exact instruction and displays runtime rejection", async ({page}) => {
  await enter(page);
  await nav(page, "Live Calls");
  await page.getByRole('button', {name:/Synthetic live session/}).click();
  await expect(page.getByRole('button', {name:'Hold',exact:true})).toBeDisabled();
  await page.getByLabel('Private agent instruction').fill('Confirm the next step.');
  await page.getByRole('button', {name:'Send instruction'}).click();
  await page.getByLabel('Operator password',{exact:true}).fill(password);
  const sent=page.waitForRequest(r=>r.url().endsWith('/control')&&r.method()==='POST');
  await page.getByRole('button', {name:'Confirm & continue'}).click();
  expect((await sent).postDataJSON()).toEqual({kind:'private_instruction',payload:{instruction:'Confirm the next step.'},confirm:true});
  await expect(page.getByRole('alert')).toContainText(/unavailable/);
  await expect(page.getByText('Private instruction queued for the agent.')).toHaveCount(0);
});
