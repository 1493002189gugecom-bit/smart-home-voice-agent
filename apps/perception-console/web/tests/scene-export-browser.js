async page => {
  await page.goto('http://127.0.0.1:8785/tests/scene-export.html');
  await page.waitForFunction(()=>typeof window.exportGLB==='function');
  const [download,result]=await Promise.all([
    page.waitForEvent('download'),
    page.evaluate(()=>window.exportGLB())
  ]);
  await download.saveAs('E:/smart-home/apps/perception-console/scene-assets/exports/smart-home-layout-2026-10-12.glb');
  return {...result,file:'E:/smart-home/apps/perception-console/scene-assets/exports/smart-home-layout-2026-10-12.glb'};
}
