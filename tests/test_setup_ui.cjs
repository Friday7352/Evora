const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
(async()=>{
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  try {
    const page=await browser.newPage({viewport:{width:1050,height:1000}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    let saved=null, job={state:'idle'};
    const html=fs.readFileSync(path.join(__dirname,'../templates/speaker_settings.html'),'utf8').replace('{{ nonce|tojson }}','"test-nonce"');
    await page.route('http://evora.test/**',async route=>{
      const req=route.request(),url=new URL(req.url());
      let body;
      if(url.pathname==='/speaker-settings'&&req.method()==='GET')return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname==='/speaker-status')body={state:'ready',device:'cuda',engine:saved?.engine||'community',enabled:true,recover_overlap:true,track_overlap:false,token_set:true};
      else if(url.pathname==='/speakers')body={speakers:[]};
      else if(url.pathname==='/speaker-settings'){
        assert.equal(req.headers()['x-evora-settings'],'test-nonce');saved=req.postDataJSON();body={state:'loading'};
      }else if(url.pathname==='/speaker-check'){
        assert.equal(req.headers()['x-evora-settings'],'test-nonce');
        if(req.method()==='POST')job={state:'complete',duration:40,processing_seconds:48,keeps_up:false,previews:[{id:'a',label:'Voice 1',start:10,end:20}],segments:[{start:10,text:'<script>bad()</script>',voice_track:{label:'Voice 1'}}]};
        body=job;
      }else return route.fulfill({status:404,body:'{}'});
      return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    await page.goto('http://evora.test/speaker-settings');
    await page.waitForFunction(()=>document.querySelector('#tokenState').textContent.includes('already saved'));
    assert(await page.locator('#jointLinks').isHidden());
    await page.selectOption('#engine','pixit');
    assert(await page.locator('#jointLinks').isVisible());
    assert.equal(await page.locator('#jointLinks a').count(),2);
    await page.fill('#token','test-secret');
    await page.click('#save');
    await page.waitForFunction(()=>document.querySelector('#token').value==='');
    assert.equal(saved.engine,'pixit');assert.equal(saved.token,'test-secret');
    await page.setInputFiles('#testAudio',{name:'conversation.wav',mimeType:'audio/wav',buffer:Buffer.from('fixture')});
    await page.click('#test');
    await page.waitForFunction(()=>document.querySelector('#checkStatus').textContent.includes('slower than live'));
    assert((await page.locator('#transcript').textContent()).includes('<script>bad()</script>'));
    assert.equal(await page.locator('#transcript script').count(),0);
    assert.equal(await page.locator('#previews button').textContent(),'Listen');
    await page.screenshot({path:path.join(__dirname,'../dist/speaker-setup-preview.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(errors,[]);
    console.log('Setup UI: mode selection, saved token, model links, remote test, speed result, safe transcript and mobile layout passed.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
