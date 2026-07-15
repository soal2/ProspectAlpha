import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, ZoomIn, ZoomOut } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getDocumentFileUrl } from "@/lib/data-service";

export interface PdfHighlight {
  page: number;
  /** PyMuPDF bbox in PDF points, top-left origin, y-down: [x0, y0, x1, y1]. */
  bbox: number[];
  label?: string;
}

interface PdfViewerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  page?: number;
  highlight?: PdfHighlight | null;
  title?: string;
}

type PdfjsModule = typeof import("pdfjs-dist");
type PdfLoadingTask = ReturnType<PdfjsModule["getDocument"]>;
type PdfDocument = Awaited<PdfLoadingTask["promise"]>;

let pdfjsPromise: Promise<PdfjsModule> | null = null;

async function loadPdfjs(): Promise<PdfjsModule> {
  if (!pdfjsPromise) {
    pdfjsPromise = import("pdfjs-dist").then((pdfjs) => {
      pdfjs.GlobalWorkerOptions.workerSrc = new URL(
        "pdfjs-dist/build/pdf.worker.min.mjs",
        import.meta.url,
      ).toString();
      return pdfjs;
    });
  }
  return pdfjsPromise;
}

export function PdfViewer({
  open,
  onOpenChange,
  documentId,
  page,
  highlight,
  title,
}: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const highlightRef = useRef<HTMLDivElement | null>(null);
  const docRef = useRef<PdfDocument | null>(null);
  const loadingTaskRef = useRef<PdfLoadingTask | null>(null);
  const renderTaskRef = useRef<{ cancel: () => void } | null>(null);

  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(page ?? 1);
  const [scale, setScale] = useState(1.25);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load the document when the dialog opens.
  useEffect(() => {
    if (!open || !documentId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const pdfjs = await loadPdfjs();
        const task = pdfjs.getDocument({
          url: getDocumentFileUrl(documentId),
          cMapUrl: "/pdfjs/cmaps/",
          cMapPacked: true,
          standardFontDataUrl: "/pdfjs/standard_fonts/",
          wasmUrl: "/pdfjs/wasm/",
        });
        loadingTaskRef.current = task;
        const doc = await task.promise;
        if (cancelled) {
          void task.destroy();
          return;
        }
        docRef.current = doc;
        setPageCount(doc.numPages);
        setCurrentPage(Math.min(Math.max(page ?? 1, 1), doc.numPages));
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "PDF 加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      docRef.current = null;
      const task = loadingTaskRef.current;
      loadingTaskRef.current = null;
      if (task) void task.destroy();
    };
  }, [open, documentId, page]);

  // Render the current page + highlight overlay.
  useEffect(() => {
    const doc = docRef.current;
    const canvas = canvasRef.current;
    if (!open || !doc || !canvas || currentPage < 1) return;
    let cancelled = false;
    (async () => {
      try {
        const pdfPage = await doc.getPage(currentPage);
        if (cancelled) return;
        const viewport = pdfPage.getViewport({ scale });
        const context = canvas.getContext("2d");
        if (!context) return;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        if (overlayRef.current) {
          overlayRef.current.style.width = `${Math.floor(viewport.width)}px`;
          overlayRef.current.style.height = `${Math.floor(viewport.height)}px`;
        }
        renderTaskRef.current?.cancel();
        const renderTask = pdfPage.render({ canvas, canvasContext: context, viewport });
        renderTaskRef.current = renderTask;
        await renderTask.promise;
        if (cancelled) return;
        // Position the highlight box. PyMuPDF bbox is PDF points with a
        // top-left origin (y-down) — the same orientation as the pdf.js
        // viewport at rotation 0, so scaling is a direct multiply. For
        // rotated pages fall back to convertToViewportPoint on the two
        // corners, which expects PDF-space (y-up) coords, hence the flip.
        const el = highlightRef.current;
        if (el) {
          if (highlight && highlight.page === currentPage && highlight.bbox?.length === 4) {
            const [x0, y0, x1, y1] = highlight.bbox;
            let left: number, top: number, width: number, height: number;
            if ((viewport.rotation ?? 0) % 360 === 0) {
              left = x0 * scale;
              top = y0 * scale;
              width = (x1 - x0) * scale;
              height = (y1 - y0) * scale;
            } else {
              const pageHeight = pdfPage.view[3] - pdfPage.view[1];
              const p1 = viewport.convertToViewportPoint(x0, pageHeight - y1) as number[];
              const p2 = viewport.convertToViewportPoint(x1, pageHeight - y0) as number[];
              left = Math.min(p1[0], p2[0]);
              top = Math.min(p1[1], p2[1]);
              width = Math.abs(p2[0] - p1[0]);
              height = Math.abs(p2[1] - p1[1]);
            }
            el.style.display = "block";
            el.style.left = `${left - 3}px`;
            el.style.top = `${top - 3}px`;
            el.style.width = `${width + 6}px`;
            el.style.height = `${height + 6}px`;
            el.scrollIntoView({ behavior: "smooth", block: "center" });
          } else {
            el.style.display = "none";
          }
        }
      } catch (err) {
        if (!cancelled && !(err instanceof Error && err.name === "RenderingCancelledException")) {
          setError(err instanceof Error ? err.message : "页面渲染失败");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, currentPage, scale, highlight, pageCount]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[90vh] max-w-5xl flex-col gap-0 p-0 sm:max-w-5xl">
        <DialogHeader className="border-b border-border px-5 py-3">
          <DialogTitle className="font-serif text-base">{title ?? "招股书原文"}</DialogTitle>
          <DialogDescription className="text-xs">
            {highlight?.label
              ? `${highlight.label} · 第 ${highlight.page} 页高亮`
              : `文档 ${documentId}`}
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2 border-b border-border px-5 py-2 text-xs">
          <button
            onClick={() => setCurrentPage((value) => Math.max(1, value - 1))}
            disabled={currentPage <= 1}
            className="inline-flex size-7 items-center justify-center rounded-md border border-border hover:bg-accent disabled:opacity-40"
            aria-label="上一页"
          >
            <ChevronLeft className="size-4" />
          </button>
          <span className="tabular">
            {currentPage} / {pageCount || "—"}
          </span>
          <button
            onClick={() => setCurrentPage((value) => Math.min(pageCount || value, value + 1))}
            disabled={pageCount > 0 && currentPage >= pageCount}
            className="inline-flex size-7 items-center justify-center rounded-md border border-border hover:bg-accent disabled:opacity-40"
            aria-label="下一页"
          >
            <ChevronRight className="size-4" />
          </button>
          <div className="ml-4 flex items-center gap-1">
            <button
              onClick={() => setScale((value) => Math.max(0.5, Number((value - 0.25).toFixed(2))))}
              className="inline-flex size-7 items-center justify-center rounded-md border border-border hover:bg-accent"
              aria-label="缩小"
            >
              <ZoomOut className="size-4" />
            </button>
            <span className="w-12 text-center tabular">{Math.round(scale * 100)}%</span>
            <button
              onClick={() => setScale((value) => Math.min(3, Number((value + 0.25).toFixed(2))))}
              className="inline-flex size-7 items-center justify-center rounded-md border border-border hover:bg-accent"
              aria-label="放大"
            >
              <ZoomIn className="size-4" />
            </button>
          </div>
          {highlight && highlight.page !== currentPage ? (
            <button
              onClick={() => setCurrentPage(highlight.page)}
              className="ml-auto rounded-full bg-amber-400/20 px-3 py-1 text-amber-700 hover:bg-amber-400/30"
            >
              跳到高亮页 P.{highlight.page}
            </button>
          ) : null}
        </div>
        <div className="flex-1 overflow-auto bg-muted/40 p-4">
          {error ? (
            <div className="grid h-full place-items-center text-sm text-destructive">{error}</div>
          ) : loading ? (
            <div className="grid h-full place-items-center text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-2">
                <Loader2 className="size-4 animate-spin" /> PDF 加载中…
              </span>
            </div>
          ) : null}
          <div
            className={`relative mx-auto w-fit shadow-lg ${loading || error ? "hidden" : ""}`}
            ref={overlayRef}
          >
            <canvas ref={canvasRef} className="block bg-white" />
            <div
              ref={highlightRef}
              style={{ display: "none" }}
              className="pointer-events-none absolute rounded-sm border-2 border-amber-500 bg-amber-400/20"
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
