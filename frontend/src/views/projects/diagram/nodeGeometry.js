/**
 * 读取可持久化的节点尺寸，不修改 Vue Flow 运行时节点。
 */
export function serializedNodeSize(node) {
  if (node?.data?.size) return node.data.size
  const width = Number(node?.dimensions?.width || 0)
  const height = Number(node?.dimensions?.height || 0)
  return width && height ? { w: width, h: height } : undefined
}
