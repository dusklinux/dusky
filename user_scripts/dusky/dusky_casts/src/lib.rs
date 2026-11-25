pub mod api;
pub mod db;
pub mod library;
pub mod player;
pub mod sync;
pub mod wire {
    pub mod api {
        include!(concat!(env!("OUT_DIR"), "/api.rs"));
    }
    pub mod sync {
        include!(concat!(env!("OUT_DIR"), "/dusksync.rs"));
    }
    // prost resolves imported package names relative to sibling modules.
    pub use sync as dusksync;
    pub mod bootstrap {
        include!(concat!(env!("OUT_DIR"), "/bootstrap.rs"));
    }
}
pub mod listening;
pub mod local;
pub mod stats;
