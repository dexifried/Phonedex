import { test, expect } from '@playwright/test';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

test('transcript fragments render as readable speaker turns and searchable phrases', async ({ page },testInfo) => {
  const transcript = [
    {speaker:'agent',text:'I can',ms:73000},
    {speaker:'assistant',text:'take',ms:73200},
    {speaker:'agent',text:'a message',ms:73500},
    {speaker:'agent',text:'for',ms:74000},
    {speaker:'agent',text:'the team',ms:74200},
    {speaker:'agent',text:'.',ms:74400},
    {speaker:'caller',text:'Thank',ms:75000},
    {speaker:'recipient',text:'you!',ms:75300},
    {speaker:'agent',text:'You',ms:76000},
    {speaker:'agent',text:"'re",ms:76100},
    {speaker:'agent',text:'welcome.',ms:76400},
    {speaker:'system',text:'Call placed on hold.',ms:77000},
    {speaker:'agent',text:'We are',ms:78000},
    {speaker:'agent',text:' back.',ms:78200},
    {speaker:'agent',text:'Anything',ms:98000},
    {speaker:'agent',text:'else?',ms:98200},
  ].map((item,i)=>({...item,event_id:`synthetic-fragment-${i}`}));
  const call={id:'synthetic_fragmented_call',contact:'Synthetic transcript example',number_masked:'••• ••• 0199',
    direction:'inbound',agent_id:null,agent_name:'Synthetic agent',provider:'Synthetic PhoneDex',state:'ended',
    started_at:'2026-10-26T00:00:00Z',ended_at:'2026-10-26T00:02:00Z',duration_seconds:null,outcome:null,
    task_success:null,cost:null,cost_kind:null,transcript,timeline:[],results:[],controls:[],backend_provider_id:'synthetic-relay'};
  await page.route('**/api/auth/session',route=>route.fulfill({json:{authenticated:true,csrf:'synthetic-csrf',reauth_until:0}}));
  await page.route('**/api/workspace',route=>route.fulfill({json:{health:{demo:true,gateway:'unknown'},stats:{},calls:[call],
    agents:[],contacts:[],providers:[],tasks:[],logs:[],activity:[],capabilities:{}}}));
  await page.route('**/api/events',route=>route.fulfill({contentType:'text/event-stream',body:': synthetic keepalive\n\n'}));
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');
  const menu=page.getByRole('button',{name:'Open navigation'});
  if(await menu.isVisible())await menu.click();
  await page.getByRole('navigation').getByRole('button',{name:'Call History',exact:true}).click();
  await page.getByRole('row').filter({hasText:'Synthetic transcript example'}).click();
  const bubbles=page.locator('.utterance');
  await expect(bubbles).toHaveCount(6);
  await expect(bubbles.nth(0).locator('p')).toHaveText('I can take a message for the team.');
  await expect(bubbles.nth(0).locator('small')).toHaveText('1:13');
  await expect(bubbles.nth(1).locator('p')).toHaveText('Thank you!');
  await expect(bubbles.nth(2).locator('p')).toHaveText("You're welcome.");
  await expect(bubbles.nth(3)).toContainText('Call placed on hold.');
  await expect(bubbles.nth(4).locator('p')).toHaveText('We are back.');
  await expect(bubbles.nth(5).locator('p')).toHaveText('Anything else?');
  await page.getByLabel('Search transcript').fill('take a message');
  await expect(bubbles).toHaveCount(1);
  await expect(bubbles.locator('p')).toHaveText('I can take a message for the team.');
  await page.getByLabel('Search transcript').fill('');
  await page.getByRole('button',{name:'Pause',exact:true}).click();
  await expect(page.getByRole('button',{name:'Resume',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Resume',exact:true}).click();
  const firstBubble=await bubbles.first().elementHandle();
  transcript.push({speaker:'agent',text:' One more thing.',ms:98400,event_id:'synthetic-fragment-live-update'});
  // EventSource reconnect opens refresh the real rendered app from the latest
  // workspace response, just as a new provider batch would.
  await expect(bubbles.nth(5).locator('p')).toHaveText('Anything else? One more thing.',{timeout:10000});
  await expect(bubbles).toHaveCount(6);
  expect(await firstBubble?.evaluate(element=>element.isConnected)).toBeTruthy();
  expect(errors).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:join(tmpdir(),`phonedex-transcript-turns-${testInfo.project.name}.png`),fullPage:true});
});
