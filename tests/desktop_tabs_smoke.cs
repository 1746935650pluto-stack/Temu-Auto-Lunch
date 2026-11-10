using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

// Isolated component checks: no application entry point, browser initialization,
// live backend, vendor calls, or user profile is used.
class DesktopTabsSmoke {
    static int checks;
    static void Check(bool value,string message){checks++;if(!value)throw new Exception(message);}
    static object Call(object instance,string name,params object[] args){return instance.GetType().GetMethod(name,BindingFlags.Instance|BindingFlags.NonPublic).Invoke(instance,args);}
    static Dictionary<string,object> Route(string page,int account,int order){return new Dictionary<string,object>{{"page",page},{"collectionAccountId",account},{"orderAccountId",order},{"title",page+" / "+account},{"apiKey","must-not-persist"}};}
    [STAThread] static int Main(string[] args){
        var assembly=Assembly.LoadFrom(Path.GetFullPath(args[0]));
        string root=Path.GetFullPath(args[1]);Directory.CreateDirectory(root);
        var program=assembly.GetType("TemuWorkbench.Program");
        program.GetField("Profile",BindingFlags.Static|BindingFlags.NonPublic).SetValue(null,root);
        program.GetField("AppIcon",BindingFlags.Static|BindingFlags.NonPublic).SetValue(null,SystemIcons.Application);
        var type=assembly.GetType("TemuWorkbench.WorkbenchWindow");
        var parse=type.GetMethod("PageRoute",BindingFlags.Static|BindingFlags.NonPublic);
        Check(parse.Invoke(null,new object[]{Route("https://example.test",1,1)})==null,"unknown page rejected");
        Check(parse.Invoke(null,new object[]{Route("orders",21,1)})==null,"invalid collection rejected");
        Check(parse.Invoke(null,new object[]{Route("orders",1,0)})==null,"invalid order account rejected");
        var opened=type.GetField("openedPages",BindingFlags.Instance|BindingFlags.NonPublic);
        var active=type.GetField("activePageKey",BindingFlags.Instance|BindingFlags.NonPublic);
        Func<Form> create=()=> (Form)Activator.CreateInstance(type,BindingFlags.Instance|BindingFlags.NonPublic,null,new object[]{new Dictionary<string,object>(),"http://127.0.0.1:8787/",true},null);
        using(var first=create()){
            Call(first,"RecordPage",Route("orders",1,4));Call(first,"RecordPage",Route("orders",2,4));
            Check(((IList)opened.GetValue(first)).Count==1,"same order account reused across collection contexts");
            Call(first,"RecordPage",Route("orders",2,5));
            Check(((IList)opened.GetValue(first)).Count==2,"different order account has separate tab");
            var old=((IList)opened.GetValue(first))[0];Call(first,"ClosePage",old);
            Check(((IList)opened.GetValue(first)).Count==1,"inactive tab closes without browser or backend");
            Call(first,"ClosePage",((IList)opened.GetValue(first))[0]);
            Check(((IList)opened.GetValue(first)).Count==1,"last tab retained");
            for(int account=1;account<=20;account++)Call(first,"RecordPage",Route("workbench",account,1));
            Check(((IList)opened.GetValue(first)).Count==16,"tab history bounded to sixteen");
            string state=File.ReadAllText(Path.Combine(root,"navigation-tabs.json"));
            Check(!state.Contains("apiKey")&&!state.Contains("must-not-persist"),"only safe route fields persisted");
            using(var second=create()){
                Check(((IList)opened.GetValue(second)).Count==16,"opened tabs restored");
                Check(Convert.ToString(active.GetValue(second))=="workbench:20","active account restored");
            }
        }
        File.WriteAllText(Path.Combine(root,"navigation-tabs.json"),"invalid-json");
        using(var bad=create())Check(((IList)opened.GetValue(bad)).Count==0,"corrupt local tab state ignored");
        Console.WriteLine("Desktop tab component checks passed: "+checks);return 0;
    }
}
