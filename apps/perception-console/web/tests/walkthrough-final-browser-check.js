async page=>{
 const errors=[],checks=[];
 const collect=message=>{if(/Content Security Policy|Couldn.t load texture|ImageBitmap|WebGL.*(error|warning)/i.test(message.text()))errors.push(message.text());};
 page.on('console',collect);
 try {
  await page.setViewportSize({width:1280,height:800});
  for(const url of ['http://127.0.0.1:8770/walkthrough','http://127.0.0.1:8771/walkthrough?tablet=1','http://127.0.0.1:8770/?viewer=walkthrough&tablet=1']) {
   const response=await page.goto(url);if(response.status()!==200)throw Error(url);
   await page.waitForFunction(()=>document.querySelector('.walkthrough-page')&&!document.querySelector('.walkthrough-loading'));
   await page.waitForTimeout(200);
   if(errors.length)throw Error(JSON.stringify(errors));
   await page.getByRole('button',{name:'进入漫游',exact:true}).click();
   const before=await page.screenshot();await page.keyboard.down('w');await page.waitForTimeout(350);await page.keyboard.up('w');
   const after=await page.screenshot();if(before.equals(after))throw Error('No visible movement '+url);
   await page.getByRole('button',{name:'全屋查看',exact:true}).click();
   await page.screenshot({path:url.includes('8771')?'output/playwright/glb-walkthrough-tablet-final.png':'output/playwright/glb-walkthrough-desktop-final.png'});
   checks.push({url,status:response.status(),walking:true,textureErrors:errors.length});
  }
  await page.goto('http://127.0.0.1:8770/');
  const entry=page.getByRole('link',{name:'模型漫游 ↗'});await entry.waitFor();await entry.click();
  await page.waitForFunction(()=>location.pathname==='/walkthrough'&&!document.querySelector('.walkthrough-loading'));
  checks.push({homeEntry:true});
  return {checks,errors};
 } finally {page.off('console',collect);}
}
