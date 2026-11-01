async page=>{
 const results=[];
 await page.route('**/assets/furniture/chair_A.gltf',route=>route.abort());
 await page.goto('http://127.0.0.1:8785/tests/scene-check.html');await page.waitForTimeout(650);
 let s=await page.evaluate(()=>scene.inspect());if(s.models.length!==19)throw Error('fallback instances should preserve entire remaining scene');results.push({case:'chair load failure',models:s.models.length});
 await page.unroute('**/assets/furniture/chair_A.gltf');
 await page.evaluate(()=>{clearInterval(keep);scene.dispose();});
 await page.route('**/assets/furniture/couch_pillows.gltf',async route=>{await page.waitForTimeout(500);await route.continue();});
 await page.reload();await page.evaluate(()=>{clearInterval(keep);scene.dispose();});await page.waitForTimeout(800);
 s=await page.evaluate(()=>scene.inspect());if(!s.disposed||s.models.length||s.animationScheduled||s.memory.geometries!==0)throw Error('late load disposal');results.push({case:'dispose during loading',...s});await page.unroute('**/assets/furniture/couch_pillows.gltf');
 for(let i=0;i<3;i++){await page.evaluate(async()=>{const m=await import('/src/home-scene.ts');window.scene=m.createHomeScene(document.querySelector('#stage'),()=>{});refresh();});await page.waitForTimeout(400);await page.evaluate(()=>scene.dispose());s=await page.evaluate(()=>scene.inspect());if(s.memory.geometries!==0||s.animationScheduled||s.memory.textures>1)throw Error('remount '+i);results.push({case:'remount '+i,memory:s.memory});}
 return results;
}
