import { test, expect } from '@playwright/test';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

test('backend diagnostics preserve history through an error and recover with manual sync', async ({ page },testInfo) => {
  let connected = true, attempts = 0;
  const backend = () => ({mode:'remote-control',status:connected?'connected':'offline',provider:'Synthetic PhoneDex',
    last_success:1792900000,local_transcript_cache:false,supported_controls:['hangup','steer']});
  await page.route('**/api/auth/session',r=>r.fulfill({json:{authenticated:true,csrf:'synthetic-csrf',reauth_until:0}}));
  await page.route('**/api/workspace',r=>r.fulfill({json:{health:{demo:false,gateway:connected?'connected':'unknown',backend:backend()},stats:{},
    calls:[],agents:[],contacts:[],providers:[],tasks:[],logs:[],activity:[],capabilities:{}}}));
  await page.route('**/api/events',r=>r.fulfill({contentType:'text/event-stream',body:': synthetic keepalive\n\n'}));
  await page.route('**/api/backend/sync',r=>{
    connected=++attempts>1;
    return r.fulfill({json:{...backend(),error:connected?null:'authentication_failed'}});
  });
  await page.goto('/');
  const menu=page.getByRole('button',{name:'Open navigation'});
  if(await menu.isVisible())await menu.click();
  await page.getByRole('navigation').getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('button',{name:'Diagnostics',exact:true}).click();
  await expect(page.getByRole('heading',{name:'PhoneDex backend'})).toBeVisible();
  await expect(page.getByText('Off · temporary display only',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Sync backend',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('rejected the operator credential');
  await page.getByRole('button',{name:'Sync backend',exact:true}).click();
  await expect(page.getByText('rejected the operator credential',{exact:false})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Sync backend',exact:true})).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:join(tmpdir(),`phonedex-backend-${testInfo.project.name}.png`),fullPage:true});
});
