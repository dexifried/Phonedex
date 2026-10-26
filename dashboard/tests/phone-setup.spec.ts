import {test,expect} from '@playwright/test';

test('phone pairing link creates a password without leaking pairing into URL or storage',async({page})=>{
  const token='synthetic-browser-pairing-token';
  let loggedIn=false;
  await page.route('**/api/auth/session',route=>route.fulfill({status:loggedIn?200:401,json:loggedIn?{authenticated:true,csrf:'synthetic-csrf',reauth_until:0}:{detail:'Authentication required'}}));
  await page.route('**/api/auth/status',route=>route.fulfill({json:{configured:false,authenticated:false,setup_required:true,pairing_required:true}}));
  let submitted:unknown;
  await page.route('**/api/auth/setup',async route=>{submitted=route.request().postDataJSON();loggedIn=true;await route.fulfill({json:{authenticated:true,csrf:'synthetic-csrf'}})});
  await page.route('**/api/workspace',route=>route.fulfill({json:{health:{demo:true,gateway:'unknown'},stats:{},calls:[],agents:[],contacts:[],providers:[],tasks:[],logs:[],activity:[],capabilities:{}}}));
  await page.route('**/api/events',route=>route.fulfill({contentType:'text/event-stream',body:': synthetic keepalive\n\n'}));
  await page.goto('/#pair='+token);
  await expect(page.getByRole('heading',{name:'Make yourself at home.'})).toBeVisible();
  await expect(page.getByLabel('Phone pairing code',{exact:true})).toHaveValue(token);
  expect(new URL(page.url()).hash).toBe('');
  await page.getByLabel('Operator password',{exact:true}).fill('Synthetic-phone-password-2026');
  await page.getByLabel('Confirm password',{exact:true}).fill('Synthetic-phone-password-2026');
  await page.getByRole('button',{name:'Create workspace'}).click();
  await expect(page.getByRole('heading',{name:'Your call operations, in focus.'})).toBeVisible();
  expect(submitted).toEqual({password:'Synthetic-phone-password-2026',pairing_token:token});
  const stored=await page.evaluate(()=>JSON.stringify({...localStorage}));
  expect(stored).not.toContain(token);
  expect(stored).not.toContain('Synthetic-phone-password-2026');
});
