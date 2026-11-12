const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'..','live-editor.js'),'utf8');
function context(rule){const context=vm.createContext({editorState:{attributeRules:[rule]},isCompositionRule:()=>false});
 const start=source.indexOf('function compositionNumberText(');vm.runInContext(source.slice(start,source.indexOf('function renderTableAttribute(',start)),context);
 const validate=source.indexOf('function validateProductAttributes(');vm.runInContext(source.slice(validate,source.indexOf('function fillEditor(',validate)),context);return context;}
test('percentage text keeps the unit outside numeric input and preserves numeric amounts',()=>{
 const rule={valueType:'TABLE',valueUnit:['%'],valueInputSum:100},c=context(rule);
 for(const [input,want] of [['100%','100'],['100％','100'],[' 60.5 % ','60.5'],[0,'0'],['0.5','0.5']])assert.equal(c.compositionNumberText(input,rule),want);
 assert.equal(c.compositionNumberText('100cm',rule),'100cm');assert.equal(c.compositionNumberText('100%',{valueUnit:['cm']}),'100%');
});
test('manual save validates digit-only percentages and rejects blank or malformed input',()=>{
 const rule={name:'材料',templatePid:1,pid:2,valueType:'TABLE',valueUnit:['%'],valueInputSum:100},c=context(rule);
 const attributes=[{templatePid:1,values:[{vid:10,numberValue:'60%'},{vid:20,numberValue:'40％'}]}];
 c.validateProductAttributes(attributes);assert.deepEqual(attributes[0].values.map(v=>v.numberValue),['60','40']);
 for(const value of ['',null,'100%%','NaN'])assert.throws(()=>c.validateProductAttributes([{templatePid:1,values:[{vid:10,numberValue:value}]}]));
});
