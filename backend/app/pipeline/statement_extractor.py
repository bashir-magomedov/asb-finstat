#!/usr/bin/env python3
"""Extract evidence-backed financial-statement tables from PDFs final."""
import argparse,json,logging,os,re,shutil
from dataclasses import dataclass,asdict,field
from pathlib import Path
#import fitz
import pymupdf as fitz
import httpx
import pytesseract
from PIL import Image
from openpyxl import Workbook
from openpyxl.styles import Font,Alignment

try:
    import pymupdf.layout
    pymupdf.layout.activate()
except Exception as exc:
    logging.warning("pymupdf_layout unavailable, falling back to line-based table detection: %s",exc)

ROOT=Path(__file__).resolve().parent
TESTS_ROOT=ROOT.parents[2]/"backend/tests"

NAMES={
    "CashFlow":("cash flow","cash-flow"),
    "Balance Sheet":("balance sheet","financial position"),
    "IncomeStatement":("income statement","profit and loss","statement of operations")}
@dataclass
class Statement:
    name:str; found:bool=False; source_pages:list[int]=field(default_factory=list); currency_and_scale:str|None=None; rows:list[list[str]]=field(default_factory=list); footnotes:list[str]=field(default_factory=list); warnings:list[str]=field(default_factory=list)

class EvidenceReader:
    def __init__(self,ocr): self.ocr=ocr; self.ready=bool(shutil.which("tesseract"))
    def read(self,path):
        doc=fitz.open(path); pages=[]
        for n,page in enumerate(doc,1):
            text=page.get_text("text",sort=True).strip()
            if len(text)<100 and self.ocr!="off" and self.ready:
                pix=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
                text=pytesseract.image_to_string(Image.frombytes("RGB",[pix.width,pix.height],pix.samples))
            pages.append(text)
        return doc,pages

class Locator:
    def __init__(self,model,use_llm): self.model=model; self.use_llm=use_llm
    def locate(self,pages):
        found={name:self._heuristic(pages,terms) for name,terms in NAMES.items()}
        if not self.use_llm or not os.getenv("OPENROUTER_API_KEY"): return found
        candidates=sorted({p for values in found.values() for p in values})
        digest="\n\n".join(f"PAGE {n}: {pages[n-1][:4000]}" for n in candidates)
        prompt='Return JSON only mapping CashFlow, Balance Sheet, IncomeStatement to page-number arrays. Identify only primary statement tables and direct continuations; never infer a missing table.\n'+digest
        try:
            response=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":"Bearer "+os.environ["OPENROUTER_API_KEY"]},json={"model":self.model,"messages":[{"role":"user","content":prompt}],"temperature":0,"max_tokens":200},timeout=45)
            response.raise_for_status(); data=json.loads(re.search(r"\{.*\}",response.json()["choices"][0]["message"]["content"],re.S).group())
            for name,terms in NAMES.items():
                proposed=[int(x) for x in data.get(name,[]) if str(x).isdigit() and 0<int(x)<=len(pages)]
                if proposed and any(t in " ".join(pages[x-1].lower() for x in proposed) for t in terms): found[name]=sorted(set(proposed))
        except Exception as exc: logging.warning("OpenRouter localization failed: %s",exc)
        return found
    @staticmethod
    def _heuristic(pages,terms):
        scores=[]
        for n,page in enumerate(pages,1):
            lower=page.lower()
            term_count=sum(lower.count(t) for t in terms)
            if not term_count: continue
            lines=[line.strip() for line in lower.splitlines() if line.strip()]
            headings=[line for line in lines if len(line)<140 and any(t in line for t in terms)]
            has_consolidated=any("consolidated" in line for line in headings)
            has_statement=any("statement" in line for line in headings)
            has_table_shape=any(len(re.findall(r"\b\d[\d,.]*\b",line)) >= 2 for line in lines)
            scores.append((has_consolidated,has_statement,has_table_shape,term_count,n))
        if not scores: return []
        table_scores=[score for score in scores if score[2]]
        if table_scores: scores=table_scores
        *_,page=max(scores,default=(False,False,False,0,0)); return [page]

class TableExtractor:
    def extract(self,doc,name,pages):
        result=Statement(name,source_pages=pages)
        for n in pages:
            page=doc[n-1]; text=page.get_text("text",sort=True)
            if not result.currency_and_scale:
                m=re.search(r"\(\s*in\s+[^)]{1,30}\)",text,re.I); result.currency_and_scale=m.group() if m else None
            result.footnotes += [x.strip() for x in text.splitlines() if re.match(r"^(notes?|note)\s*[:(]",x.strip(),re.I)]
            try:
                tables=page.find_tables(strategy="text").tables
                rows=max((t.extract() for t in tables),key=lambda x:sum(len(r) for r in x),default=[])
            except Exception: rows=[]
            result.rows += rows or [[x.strip()] for x in text.splitlines() if x.strip()]
        result.found=bool(result.rows)
        if not result.found: result.warnings.append("No extractable table was recovered.")
        return result

class Writer:
    def write(self,pdf,out,items):
        out.mkdir(parents=True,exist_ok=True); base=out/pdf.stem
        payload={"source_pdf":pdf.name,"statements":{k:asdict(v) for k,v in items.items()},"missing_statements":[k for k,v in items.items() if not v.found]}
        (base.with_suffix(".json")).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        wb=Workbook(); wb.remove(wb.active)
        for name,item in items.items():
            ws=wb.create_sheet(name); ws["A1"]=name; ws["A1"].font=Font(bold=True,size=14)
            ws["A2"]=f"Source pages: {item.source_pages or 'not found'}"; ws["A3"]=f"Currency and scale: {item.currency_and_scale or 'not identified'}"
            if item.found:
                for r,row in enumerate(item.rows,5):
                    for c,value in enumerate(row,1): ws.cell(r,c,value or "").alignment=Alignment(wrap_text=True,vertical="top")
                row=6+len(item.rows); ws.cell(row,1,"Footnotes").font=Font(bold=True)
                for i,note in enumerate(item.footnotes,1): ws.cell(row+i,1,note)
            else: ws["A5"]="NOT FOUND"
            ws.column_dimensions["A"].width=58; ws.freeze_panes="A5"
        wb.save(base.with_suffix(".xlsx")); return base

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input-file",type=Path); p.add_argument("--input-dir",type=Path,default=TESTS_ROOT/"inputs"); p.add_argument("--output-dir",type=Path,default=TESTS_ROOT/"outputs"); p.add_argument("--model",default="google/gemini-2.5-flash"); p.add_argument("--ocr-mode",choices=("auto","off","required"),default="auto"); p.add_argument("--no-openrouter",action="store_true"); a=p.parse_args()
    files=[a.input_file] if a.input_file else sorted(a.input_dir.glob("*.pdf"))
    if not files: raise SystemExit("No PDFs found...")
    for pdf in files:
        doc,pages=EvidenceReader(a.ocr_mode).read(pdf)
        try:
            locations=Locator(a.model,not a.no_openrouter).locate(pages); ext=TableExtractor(); items={n:ext.extract(doc,n,locations[n]) for n in NAMES}; base=Writer().write(pdf,a.output_dir,items); print(f"Created {base.with_suffix('.xlsx')} and {base.with_suffix('.json')}")
        finally: doc.close()
if __name__=="__main__": main()
