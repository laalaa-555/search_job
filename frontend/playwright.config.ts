import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'./tests', fullyParallel:true, workers:2, timeout:30000,
  use:{baseURL:process.env.E2E_BASE_URL || 'http://127.0.0.1:3000', screenshot:'only-on-failure', trace:'retain-on-failure'},
  projects:[{name:'desktop', use:{browserName:'chromium', viewport:{width:1100,height:1000}}},{name:'mobile', use:{browserName:'chromium', viewport:{width:390,height:844}, isMobile:true, hasTouch:true}}],
});
