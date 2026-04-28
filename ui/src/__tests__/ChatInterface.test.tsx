import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const clientMocks = vi.hoisted(() => ({
  apiGetChatHistory: vi.fn(),
  apiChatStream: vi.fn(),
  apiChat: vi.fn(),
  apiClearChatHistory: vi.fn(),
  apiUploadNote: vi.fn(),
  apiGetLatestPov: vi.fn(),
  apiGetLatestJep: vi.fn(),
  apiGetLatestWaf: vi.fn(),
}));

vi.mock('../api/client', () => ({
  apiGetChatHistory: clientMocks.apiGetChatHistory,
  apiChatStream: clientMocks.apiChatStream,
  apiChat: clientMocks.apiChat,
  apiClearChatHistory: clientMocks.apiClearChatHistory,
  apiUploadNote: clientMocks.apiUploadNote,
  apiGetLatestPov: clientMocks.apiGetLatestPov,
  apiGetLatestJep: clientMocks.apiGetLatestJep,
  apiGetLatestWaf: clientMocks.apiGetLatestWaf,
}));

import { ChatInterface } from '../components/ChatInterface';

describe('ChatInterface quick actions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    clientMocks.apiGetChatHistory.mockResolvedValue({ history: [] });
    clientMocks.apiChat.mockResolvedValue({ reply: '', tool_calls: [], artifacts: {}, artifact_manifest: { downloads: [] } });
    clientMocks.apiClearChatHistory.mockResolvedValue({});
    clientMocks.apiUploadNote.mockResolvedValue({});
    clientMocks.apiGetLatestPov.mockResolvedValue({ content: '' });
    clientMocks.apiGetLatestJep.mockResolvedValue({ content: '' });
    clientMocks.apiGetLatestWaf.mockResolvedValue({ content: '' });
    vi.restoreAllMocks();
  });

  it('shows Archie hello and working messages', async () => {
    let resolveStream: (value: {
      reply: string;
      tool_calls: unknown[];
      artifacts: Record<string, unknown>;
      artifact_manifest: { downloads: unknown[] };
    }) => void = () => {};
    clientMocks.apiChatStream.mockImplementation(() => new Promise(resolve => {
      resolveStream = resolve;
    }));

    render(<ChatInterface />);

    await userEvent.type(screen.getByTestId('chat-customer-id'), 'acme');

    await waitFor(() => {
      expect(screen.getByTestId('archie-hello-message')).toHaveTextContent("Hi, I'm Archie.");
    });

    await userEvent.type(screen.getByTestId('chat-input'), 'hello archie');
    await userEvent.click(screen.getByTestId('chat-send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('archie-working-message')).toHaveTextContent(/Archie is|Archie here|Archie is on it/);
    });

    resolveStream({
      reply: 'Hello. I can help.',
      tool_calls: [],
      artifacts: {},
      artifact_manifest: { downloads: [] },
    });

    await waitFor(() => {
      expect(screen.queryByTestId('archie-working-message')).not.toBeInTheDocument();
    });
  });

  it('renders checkpoint actions and sends the selected reply', async () => {
    clientMocks.apiChatStream
      .mockResolvedValueOnce({
        reply: 'Cost checkpoint required before final acceptance.\n- Reply `approve checkpoint` to accept this tradeoff or revise the request and rerun.',
        tool_calls: [
          {
            tool: 'generate_bom',
            args: {},
            result_summary: 'Checkpoint required',
            result_data: {
              checkpoint: {
                options: ['approve checkpoint', 'revise input'],
              },
            },
          },
        ],
        artifacts: {},
        artifact_manifest: { downloads: [] },
      })
      .mockResolvedValueOnce({
        reply: 'Checkpoint approved. I recorded the decision and cleared the pending tradeoff review.',
        tool_calls: [],
        artifacts: {},
        artifact_manifest: { downloads: [] },
      });

    render(<ChatInterface />);

    await userEvent.type(screen.getByTestId('chat-customer-id'), 'acme');
    await userEvent.type(screen.getByTestId('chat-customer-name'), 'Acme');
    await userEvent.type(screen.getByTestId('chat-input'), 'please review this');
    await userEvent.click(screen.getByTestId('chat-send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('quick-action-approve-checkpoint')).toBeInTheDocument();
      expect(screen.getByTestId('quick-action-revise-input')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId('quick-action-approve-checkpoint'));

    await waitFor(() => {
      expect(clientMocks.apiChatStream).toHaveBeenCalledTimes(2);
    });

    expect(clientMocks.apiChatStream.mock.calls[1][2]).toBe('approve checkpoint');
    expect(screen.getAllByTestId('chat-user-message').at(-1)).toHaveTextContent('approve checkpoint');
  });

  it('renders update workflow actions from assistant text', async () => {
    clientMocks.apiChatStream.mockResolvedValue({
      reply: 'An update workflow is waiting for confirmation.\n- Reply `confirm update all` to proceed or `cancel update` to stop.',
      tool_calls: [],
      artifacts: {},
      artifact_manifest: { downloads: [] },
    });

    render(<ChatInterface />);

    await userEvent.type(screen.getByTestId('chat-customer-id'), 'acme');
    await userEvent.type(screen.getByTestId('chat-input'), 'update everything');
    await userEvent.click(screen.getByTestId('chat-send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('quick-action-confirm-update-all')).toBeInTheDocument();
      expect(screen.getByTestId('quick-action-cancel-update')).toBeInTheDocument();
    });
  });

  it('keeps scrolling inside the chat pane instead of forcing the page to jump', async () => {
    const scrollToMock = vi.fn();
    const scrollIntoViewMock = vi.fn();
    if (!HTMLElement.prototype.scrollTo) {
      Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
        configurable: true,
        value: () => {},
      });
    }
    vi.spyOn(HTMLElement.prototype, 'scrollTo').mockImplementation(scrollToMock);
    vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(scrollIntoViewMock);

    clientMocks.apiChatStream.mockResolvedValue({
      reply: 'Diagram generated.',
      tool_calls: [],
      artifacts: {},
      artifact_manifest: { downloads: [] },
    });

    render(<ChatInterface />);

    const customerId = screen.getByTestId('chat-customer-id');
    const input = screen.getByTestId('chat-input');
    await userEvent.type(customerId, 'acme');
    await userEvent.type(input, 'generate a diagram');

    const thread = screen.getByText(/No messages yet/i).parentElement as HTMLDivElement;
    Object.defineProperty(thread, 'scrollHeight', { configurable: true, value: 1200 });
    Object.defineProperty(thread, 'clientHeight', { configurable: true, value: 600 });
    Object.defineProperty(thread, 'scrollTop', { configurable: true, writable: true, value: 580 });

    await userEvent.click(screen.getByTestId('chat-send-button'));

    await waitFor(() => {
      expect(scrollToMock).toHaveBeenCalled();
    });
    expect(scrollIntoViewMock).not.toHaveBeenCalled();
  });
});
