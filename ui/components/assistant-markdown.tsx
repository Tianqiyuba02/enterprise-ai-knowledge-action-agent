import ReactMarkdown from "react-markdown";

/**
 * Render the deliberately small Markdown subset used by governed answers.
 * Links stay in the server-built citation list, and raw HTML is never rendered.
 */
export function AssistantMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      allowedElements={["p", "ul", "ol", "li", "strong", "em", "code", "pre", "blockquote", "br"]}
      skipHtml
      unwrapDisallowed
    >
      {children}
    </ReactMarkdown>
  );
}
