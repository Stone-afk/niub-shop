import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ChatPage from "./page";

const api = vi.hoisted(() => ({
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getConversation: vi.fn(),
  streamConversationTurn: vi.fn(),
  retryConversationTurn: vi.fn(),
}));
const productApi = vi.hoisted(() => ({ listProducts: vi.fn() }));

vi.mock("@/lib/conversations", () => api);
vi.mock("@/lib/products", () => productApi);
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const products = [
  {
    id: "mug",
    name: "陶瓷杯 / Ceramic Mug",
    description: "适合咖啡 / For coffee",
    price_cents: 39000,
    currency: "TWD",
    category: "drinkware",
    tags: ["coffee"],
    availability: "in_stock" as const,
  },
  {
    id: "tote",
    name: "帆布袋 / Canvas Tote",
    description: "耐用托特包 / Durable tote",
    price_cents: 59000,
    currency: "TWD",
    category: "bags",
    tags: ["durable"],
    availability: "out_of_stock" as const,
  },
];

const recent = {
  id: "recent",
  title: "最近对话",
  created_at: "2026-11-05T00:00:00+00:00",
  updated_at: "2026-11-05T00:02:00+00:00",
  last_turn_status: "failed" as const,
};
const older = {
  id: "older",
  title: "较早对话",
  created_at: "2026-11-05T00:00:00+00:00",
  updated_at: "2026-11-05T00:01:00+00:00",
  last_turn_status: "completed" as const,
};
const details = {
  recent: {
    ...recent,
    turns: [
      {
        id: "done",
        user_content: "推荐商品",
        assistant_content: "推荐咖啡杯",
        status: "completed" as const,
        error_code: null,
        created_at: recent.created_at,
        updated_at: recent.updated_at,
        recommended_product_ids: ["mug", "tote"],
      },
      {
        id: "failed",
        user_content: "还有吗",
        assistant_content: null,
        status: "failed" as const,
        error_code: "model_stream_failed",
        created_at: recent.created_at,
        updated_at: recent.updated_at,
        recommended_product_ids: [],
      },
    ],
  },
  older: { ...older, turns: [] },
};

beforeEach(() => {
  vi.resetAllMocks();
  api.listConversations.mockResolvedValue([recent, older]);
  api.getConversation.mockImplementation((id: string) =>
    Promise.resolve(details[id as keyof typeof details]),
  );
  api.streamConversationTurn.mockResolvedValue(undefined);
  api.retryConversationTurn.mockResolvedValue(undefined);
  api.deleteConversation.mockResolvedValue(undefined);
  productApi.listProducts.mockResolvedValue(products);
});

describe("persisted conversation page", () => {
  it("loads recent history, renders completed and failed turns, and is bilingual", async () => {
    render(<ChatPage />);

    expect(screen.getByText("正在加载历史… / Loading history…")).toBeVisible();
    expect(await screen.findByText("推荐咖啡杯")).toBeVisible();
    expect(screen.getByText("回复失败，可重试 / Reply failed, retry available")).toBeVisible();
    expect(screen.getByRole("button", { name: "重试 / Retry" })).toBeVisible();
    expect(screen.getByRole("button", { name: /最近对话/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("navigation", { name: "历史对话 / Conversation history" })).toBeVisible();
    expect(screen.getByRole("link", { name: "返回商城 / Back to store" })).toHaveAttribute("href", "/");
    const cards = await screen.findByRole("list", { name: "推荐商品 / Recommended products" });
    expect(cards).toHaveTextContent("陶瓷杯 / Ceramic Mug");
    expect(cards).toHaveTextContent("帆布袋 / Canvas Tote");
    expect(cards).toHaveTextContent(/TWD\s*390\.00/);
    expect(cards).toHaveTextContent("有货 / In stock");
    expect(cards).toHaveTextContent("暂时缺货 / Out of stock");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(cards.querySelectorAll("a")).toHaveLength(2);
    expect(cards.querySelector('a[href="/products/mug"]')).toBeTruthy();
  });

  it("selects and loads an older conversation", async () => {
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("推荐咖啡杯");

    await user.click(screen.getByRole("button", { name: /较早对话/ }));

    await waitFor(() => expect(api.getConversation).toHaveBeenCalledWith("older"));
    expect(screen.getByText("开始一段对话 / Start a conversation")).toBeVisible();
  });

  it("deletes a history conversation and selects the next available one", async () => {
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("推荐咖啡杯");

    await user.click(screen.getAllByRole("button", { name: "删除对话 / Delete conversation" })[0]);

    await waitFor(() => expect(api.deleteConversation).toHaveBeenCalledWith("recent"));
    expect(screen.queryByRole("button", { name: /最近对话/ })).not.toBeInTheDocument();
    expect(await screen.findByText("开始一段对话 / Start a conversation")).toBeVisible();
  });

  it("keeps new chat unsaved until the first message, then refreshes server state", async () => {
    const created = { ...older, id: "created", title: "新对话 / New conversation" };
    api.createConversation.mockResolvedValue(created);
    api.getConversation.mockImplementation((id: string) =>
      Promise.resolve(id === "created" ? { ...created, turns: [] } : details[id as keyof typeof details]),
    );
    api.listConversations.mockResolvedValueOnce([recent, older]).mockResolvedValueOnce([
      { ...created, title: "第一问", last_turn_status: "completed" }, recent, older,
    ]);
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("推荐咖啡杯");

    await user.click(screen.getByRole("button", { name: "新对话 / New chat" }));
    expect(api.createConversation).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("消息 / Message"), "第一问");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));

    await waitFor(() => expect(api.createConversation).toHaveBeenCalledTimes(1));
    expect(api.streamConversationTurn).toHaveBeenCalledWith("created", "第一问", expect.any(Function), expect.any(Function), expect.any(Function));
    await waitFor(() => expect(api.listConversations).toHaveBeenCalledTimes(2));
  });

  it("continues an existing conversation with only the new content", async () => {
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("推荐咖啡杯");

    await user.type(screen.getByLabelText("消息 / Message"), "继续问");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));

    await waitFor(() => expect(api.streamConversationTurn).toHaveBeenCalledWith("recent", "继续问", expect.any(Function), expect.any(Function), expect.any(Function)));
    expect(api.createConversation).not.toHaveBeenCalled();
  });

  it("retries the latest failed turn and reloads authoritative history", async () => {
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("回复失败，可重试 / Reply failed, retry available");

    await user.click(screen.getByRole("button", { name: "重试 / Retry" }));

    await waitFor(() => expect(api.retryConversationTurn).toHaveBeenCalledWith("recent", "failed", expect.any(Function), expect.any(Function), expect.any(Function)));
    await waitFor(() => expect(api.getConversation).toHaveBeenCalledTimes(2));
  });

  it("clears a stale operation error after a later turn succeeds", async () => {
    api.streamConversationTurn
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(undefined);
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("推荐咖啡杯");

    await user.type(screen.getByLabelText("消息 / Message"), "第一次发送");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    expect(await screen.findByText("操作失败，请重试 / Operation failed, please retry")).toBeVisible();

    await user.type(screen.getByLabelText("消息 / Message"), "第二次发送");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    await waitFor(() => expect(screen.queryByText("操作失败，请重试 / Operation failed, please retry")).not.toBeInTheDocument());
  });

  it("does not show an operation error when the stream succeeds but refresh is temporarily unavailable", async () => {
    let conversationReads = 0;
    api.getConversation.mockImplementation((id: string) => {
      conversationReads += 1;
      if (conversationReads === 2) return Promise.reject(new Error("refresh unavailable"));
      return Promise.resolve(details[id as keyof typeof details]);
    });
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("推荐咖啡杯");

    await user.type(screen.getByLabelText("消息 / Message"), "成功回答");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    await waitFor(() => expect(api.streamConversationTurn).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText("操作失败，请重试 / Operation failed, please retry")).not.toBeInTheDocument());
  });

  it("keeps stream deltas attached to their original conversation after selection changes", async () => {
    let sendDelta!: (value: string) => void;
    let finish!: () => void;
    api.streamConversationTurn.mockImplementation((_id: string, _content: string, onDelta: (value: string) => void) => {
      sendDelta = onDelta;
      return new Promise<void>((resolve) => { finish = resolve; });
    });
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("推荐咖啡杯");
    await user.type(screen.getByLabelText("消息 / Message"), "流式问题");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));
    await user.click(screen.getByRole("button", { name: /较早对话/ }));

    await act(async () => sendDelta("属于最近会话"));
    expect(screen.queryByText("属于最近会话")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /最近对话/ }));
    expect(screen.getByText("属于最近会话")).toBeVisible();
    await act(async () => finish());
  });

  it("keeps live recommendation cards attached to their original conversation", async () => {
    let sendProducts!: (ids: string[]) => void;
    let finish!: () => void;
    api.streamConversationTurn.mockImplementation(
      (_id: string, _content: string, _onDelta: (value: string) => void, onProducts: (ids: string[]) => void) => {
        sendProducts = onProducts;
        return new Promise<void>((resolve) => { finish = resolve; });
      },
    );
    const user = userEvent.setup();
    render(<ChatPage />);
    await screen.findByText("推荐咖啡杯");
    await user.type(screen.getByLabelText("消息 / Message"), "流式推荐");
    await user.click(screen.getByRole("button", { name: "发送 / Send" }));

    await act(async () => sendProducts(["mug"]));
    expect(screen.getAllByText("陶瓷杯 / Ceramic Mug").length).toBeGreaterThan(1);
    await user.click(screen.getByRole("button", { name: /较早对话/ }));
    expect(screen.queryByText("陶瓷杯 / Ceramic Mug")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /最近对话/ }));
    expect(screen.getAllByText("陶瓷杯 / Ceramic Mug").length).toBeGreaterThan(1);
    await act(async () => finish());
  });

  it("shows a bilingual placeholder for a catalog-missing historical id", async () => {
    api.getConversation.mockResolvedValueOnce({
      ...details.recent,
      turns: [{ ...details.recent.turns[0], recommended_product_ids: ["removed-id"] }],
    });
    render(<ChatPage />);

    expect(await screen.findByText("商品暂不可用 / Product unavailable")).toBeVisible();
    expect(screen.getByText("removed-id")).toBeVisible();
    expect(screen.getByText("推荐咖啡杯")).toBeVisible();
  });

  it("keeps text available when catalog loading fails", async () => {
    productApi.listProducts.mockRejectedValueOnce(new Error("offline"));
    render(<ChatPage />);

    expect(await screen.findByText("推荐咖啡杯")).toBeVisible();
    expect(screen.getByText("商品信息加载失败 / Failed to load products")).toBeVisible();
  });

  it("shows bilingual empty and list-error states", async () => {
    api.listConversations.mockResolvedValueOnce([]);
    const { unmount } = render(<ChatPage />);
    expect(await screen.findByText("还没有历史对话 / No conversation history yet")).toBeVisible();
    unmount();

    api.listConversations.mockRejectedValueOnce(new Error("offline"));
    render(<ChatPage />);
    expect(await screen.findByRole("alert", { name: "历史加载失败 / Failed to load history" })).toBeVisible();
  });
});
