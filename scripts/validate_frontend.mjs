import fs from "node:fs";

const html=fs.readFileSync("index.html","utf8");
const matches=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)];
if(!matches.length) throw new Error("Nenhum script inline encontrado");
for(const [i,m] of matches.entries()){
  try{
    new Function(m[1]);
  }catch(err){
    throw new Error(`Erro de sintaxe no script inline ${i+1}: ${err.message}`);
  }
}
console.log(`FRONTEND_JS_OK scripts=${matches.length}`);


const requiredFunctions=["setConnection","loadOfficialData","ensureOperationalMap","buildOperationalMapLayers","activateMapMode","updateOperationalMapSummary","renderDefenseCivilPanel","groupDefenseCivilHistory","renderDefenseCivilHistory","loadClimateHistory","loadReferenceStationHistory","groupReferenceStationDaily","renderReferenceStationHistory","loadHistory","renderHistory","groupRiskHistory","groupPluvioHourly","renderRiskHistory","renderPluvioHistory"];
for(const fn of requiredFunctions){
  const re=new RegExp("function\\s+"+fn+"\\s*\\(");
  if(!re.test(html)){
    throw new Error("Função obrigatória ausente no frontend: "+fn);
  }
}
console.log("FRONTEND_RUNTIME_HELPERS_OK "+requiredFunctions.join(","));
