import { createContext, useContext } from 'react';

type ChatContextValue = {
    incidentId: string | null;
    setIncidentId: (id: string | null) => void;
};

export const ChatContext = createContext<ChatContextValue>({
    incidentId: null,
    setIncidentId: () => {},
});

export function useChatContext() {
    return useContext(ChatContext);
}
