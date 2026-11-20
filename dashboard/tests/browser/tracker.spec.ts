import { test, expect } from '@playwright/test';

test('keeps workflow evidence visible during a partial GitHub outage', async ({page}) => {
  await page.goto('/?repo=example/partial');
  await expect(page.getByText('No workflow runs on this branch.')).toBeVisible();
  await expect(page.getByText('Default-branch commit unavailable.')).toBeVisible();
  await expect(page.getByText('Release data unavailable.', {exact:true})).toBeVisible();
  await expect(page.getByText('No published releases.', {exact:true})).toHaveCount(0);
});

test('opens the public tracker when a bookmark contains an unknown notice', async ({page}) => {
  const response = await page.goto('/?notice=constructor');
  expect(response?.status()).toBe(200);
  await expect(page.getByRole('heading', {name:'Track a repository'})).toBeVisible();
  await expect(page.getByRole('link', {name:'Open personal workspace'})).toHaveCount(0);
});

test('explains invalid repository links and form entries', async ({page}) => {
  await page.goto('/?repo=invalid');
  const alert = page.getByText('Enter a GitHub repository URL or owner/repository.');
  await expect(alert).toBeVisible();
  await page.getByLabel('GitHub repository').fill('invalid');
  await page.getByRole('button', {name:'Inspect repository'}).click();
  await expect(page).toHaveURL(/notice=name$/);
  await expect(alert).toBeVisible();
});

test('distinguishes missing repositories from a GitHub request limit', async ({page}) => {
  await page.goto('/?repo=example/missing');
  await expect(page.getByRole('alert').filter({hasText:'Repository unavailable'})).toBeVisible();
  await page.getByLabel('GitHub repository').fill('example/limited');
  await page.getByRole('button', {name:'Inspect repository'}).click();
  await expect(page.getByRole('alert').filter({hasText:'request limit was reached'})).toBeVisible();
});

test('retains release evidence when workflow history is unavailable', async ({page}) => {
  await page.goto('/');
  await page.getByLabel('GitHub repository').fill('example/release');
  await page.getByRole('button', {name:'Inspect repository'}).click();
  await expect(page.getByRole('link', {name:'v1.0', exact:true})).toBeVisible();
  const success = page.locator('article').filter({has:page.getByRole('heading', {name:'Workflow success'})});
  await expect(success).toContainText('Workflow data unavailable');
  await expect(success).not.toContainText('0 of 0');
  await page.getByRole('link', {name:'Refresh measurements'}).click();
  await expect(page.getByRole('heading', {name:'Published releases'})).toBeVisible();
});
