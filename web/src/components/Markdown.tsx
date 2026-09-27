import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** remark 插件：把未能核对到的数字包成 <mark>，提醒人工复核 */
function remarkMarks(marks: string[]) {
  return () => (tree: any) => {
    if (!marks.length) return;
    const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp("(" + marks.map(esc).sort((a, b) => b.length - a.length).join("|") + ")");
    const walk = (node: any) => {
      if (!node.children) return;
      const out: any[] = [];
      for (const c of node.children) {
        if (c.type === "text" && re.test(c.value)) {
          c.value.split(re).forEach((part: string, i: number) => {
            if (!part) return;
            out.push(i % 2 ? { type: "mark", data: { hName: "mark", hProperties: { title: "未能在数据中核对到此数字" } }, children: [{ type: "text", value: part }] }
              : { type: "text", value: part });
          });
        } else { walk(c); out.push(c); }
      }
      node.children = out;
    };
    walk(tree);
  };
}

/** 注意：单个「~」常用来写日期区间（09-14~09-20），不能当删除线；只有「~~文字~~」才是删除线 */
export default function Markdown({ text, marks = [] }: { text: string; marks?: string[] }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[[remarkGfm, { singleTilde: false }], remarkMarks(marks)]}>{text || ""}</ReactMarkdown>
    </div>
  );
}
