// Release the old view immediately when a user selects a different flight.
// The package reader can finish caching its response independently.
export function createEvaluationRead() {
    let finishPending;
    return {
        read(load) {
            let cancel;
            const cancelled = new Promise(resolve => { cancel = () => resolve(null); });
            finishPending = cancel;
            return Promise.race([Promise.resolve().then(load), cancelled]).finally(() => {
                if (finishPending === cancel) finishPending = null;
            });
        },
        cancel() {
            finishPending?.();
            finishPending = null;
        },
    };
}
