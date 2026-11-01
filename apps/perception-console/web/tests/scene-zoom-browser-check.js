async page => {
  await page.goto('http://127.0.0.1:8785/tests/scene-check.html');
  await page.waitForFunction(()=>scene.inspect().models.length===21);
  await page.waitForTimeout(500);
  const inspect=()=>page.evaluate(()=>scene.inspect().devices);
  const checks=[];
  let devices=await inspect();
  if(devices.some(d=>d.labelVisible)) throw Error('wide view must hide captions');
  checks.push('default wide view hides device captions');
  await page.screenshot({path:'output/playwright/kaykit-zoom-wide.png'});
  const rect=await page.locator('canvas').boundingBox();
  await page.mouse.move(rect.x+rect.width/2,rect.y+rect.height/2);
  let transition=false, close=false;
  for(let i=0;i<24;i++) {
    await page.mouse.wheel(0,-100);await page.waitForTimeout(60);
    devices=await inspect();
    const alpha=devices[0].labelOpacity;
    if(alpha>0.01&&alpha<0.99) transition=true;
    if(alpha===1) {close=true;break;}
  }
  if(!transition||!close||!devices.every(d=>d.labelVisible)) throw Error('wheel zoom must reveal captions gradually');
  checks.push('wheel zoom gradually reveals captions');
  checks.push('close view fully shows captions');
  if(!devices.find(d=>d.id==='living_room_ac').airVisible || devices.find(d=>d.id==='living_room_light').current!==1) throw Error('zoom changed confirmed effects');
  checks.push('label zoom does not alter device state or airflow');
  await page.screenshot({path:'output/playwright/kaykit-zoom-close.png'});
  for(let i=0;i<24;i++) {await page.mouse.wheel(0,100);await page.waitForTimeout(40);}
  if((await inspect()).some(d=>d.labelVisible)) throw Error('zoom out must hide captions again');
  checks.push('zooming out hides captions again');
  await page.evaluate(()=>{clearInterval(keep);scene.dispose();});
  return {checks};
}
