async page=>{
  await page.goto('http://127.0.0.1:8785/tests/walkthrough-check.html');
  await page.setViewportSize({width:1280,height:800});
  await page.getByRole('button',{name:'进入漫游',exact:true}).waitFor();
  await page.waitForFunction(()=>!document.querySelector('.walkthrough-loading'));
  const checks=[],check=(pass,name)=>{if(!pass)throw Error(name);checks.push(name);};
  const pose=()=>page.evaluate(()=>({position:renderedCamera.position.toArray(),rotation:renderedCamera.rotation.toArray().slice(0,3)}));
  const count=await page.evaluate(()=>{let furniture=0,rooms=0;renderedScene.traverse(o=>{if(o.userData.asset)furniture++;if(o.userData.roomId)rooms++;});return {furniture,rooms};});
  check(count.furniture===21&&count.rooms===3,'real GLB opens with 21 furniture instances and 3 rooms');
  let initial=await pose();await page.keyboard.press('w');
  check(JSON.stringify(await pose())===JSON.stringify(initial),'overview does not consume walking keys');
  await page.screenshot({path:'output/playwright/glb-walkthrough-overview.png'});
  await page.getByRole('button',{name:'进入漫游',exact:true}).click();
  initial=await pose();
  await page.keyboard.down('w');await page.waitForTimeout(400);await page.keyboard.up('w');
  const advanced=await pose();
  check(advanced.position[2]>initial.position[2]+0.08,'W walks forward relative to the view');
  await page.waitForTimeout(150);
  check(JSON.stringify(await pose())===JSON.stringify(advanced),'key release stops walking');
  const canvas=await page.locator('canvas').boundingBox();
  await page.mouse.move(canvas.x+canvas.width*.5,canvas.y+canvas.height*.55);await page.mouse.down();
  await page.mouse.move(canvas.x+canvas.width*.62,canvas.y+canvas.height*.58,{steps:5});await page.mouse.up();
  check(Math.abs((await pose()).rotation[1]-advanced.rotation[1])>0.1,'drag looks around without moving the body');
  await page.getByRole('button',{name:'重置视角',exact:true}).click();
  const reset=await pose();check(Math.abs(reset.position[2]-initial.position[2])<1e-6,'reset returns to a safe room entry');
  for(const [name,range] of [['客厅',[-2.95,2.95]],['卧室',[-6.605,-3.055]],['厨房',[-.27,2.95]]]) {
    await page.getByRole('button',{name,exact:true}).click();
    const current=await pose();check(current.position[0]>range[0]&&current.position[0]<range[1],'room entry: '+name);
    await page.screenshot({path:'output/playwright/glb-walkthrough-'+name+'.png'});
  }
  await page.getByRole('button',{name:'客厅',exact:true}).click();
  const beforePad=await pose(),pad=await page.getByRole('button',{name:'向前',exact:true}).boundingBox();
  await page.mouse.move(pad.x+pad.width/2,pad.y+pad.height/2);await page.mouse.down();await page.waitForTimeout(350);await page.mouse.up();
  check((await pose()).position[2]>beforePad.position[2]+.07,'on-screen movement pad moves the camera');
  await page.getByRole('button',{name:'重置视角',exact:true}).click();
  const touchStart=await pose();
  const touch=await page.context().newCDPSession(page);
  const padPoint={id:1,x:Math.round(pad.x+pad.width/2),y:Math.round(pad.y+pad.height/2)};
  const lookPoint={id:2,x:Math.round(canvas.x+canvas.width*.5),y:Math.round(canvas.y+canvas.height*.55)};
  await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[padPoint]});await page.waitForTimeout(250);
  check((await pose()).position[2]>touchStart.position[2]+.05,'touch press walks forward');
  await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[padPoint,lookPoint]});
  lookPoint.x+=90;await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[padPoint,lookPoint]});
  const looked=await pose();check(Math.abs(looked.rotation[1]-touchStart.rotation[1])>.1,'two fingers can walk and look simultaneously');
  await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[padPoint]});
  lookPoint.x+=70;await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[lookPoint]});
  await page.waitForTimeout(70);
  check(Math.abs((await pose()).rotation[1]-looked.rotation[1])>.1,'releasing movement does not cancel the held look finger');
  await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.detach();
  await page.keyboard.down('w');await page.waitForTimeout(100);
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
  const hidden=await pose();await page.waitForTimeout(160);check(JSON.stringify(await pose())===JSON.stringify(hidden),'hidden page clears held input and pauses movement');
  await page.keyboard.up('w');await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));});
  for(const [width,height] of [[1280,800],[1024,768],[768,1024],[390,844]]) {
    await page.setViewportSize({width,height});
    const layout=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,vertical:document.documentElement.scrollHeight>innerHeight+1}));
    check(!layout.overflow&&!layout.vertical,'responsive walkthrough: '+width+'x'+height);
  }
  await page.evaluate(()=>unmount());
  const disposed=await page.evaluate(()=>renderedRenderer.info.memory);
  check(disposed.geometries===0&&disposed.textures<=1,'unmount frees model geometry and application textures');
  await page.route('**/assets/models/*.glb',r=>r.abort());
  await page.reload();await page.getByRole('button',{name:'重新加载',exact:true}).waitFor();
  check(true,'model load failure is visible and retryable');await page.unroute('**/assets/models/*.glb');
  await page.getByRole('button',{name:'重新加载',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('.walkthrough-loading'));
  check(true,'retry loads the model after a network failure');
  return {checks,count,disposed};
}
